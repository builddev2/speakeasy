"""Guided pronunciation training for a profile.

The default flow is read-aloud: Speakeasy suggests short sessions of prompts
(everyday phrases first, then the words the model most often mishears), the
user reads each one with the hold-to-talk hotkey, and whenever a *targeted*
word is misheard the heard form is saved to the profile as a correction. A
Custom option keeps the older type-your-own-words path for personal names.
"""

import queue
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from difflib import SequenceMatcher

from . import config
from .hotkey import HotkeyListener
from .profiles import Profile, normalize
from .recorder import Recorder
from .training_content import COMMON_WORDS, SESSIONS
from .transcriber import Transcriber

# Per prompt: stop early after this many clean takes, cap usable takes, and
# cap total attempts (usable + empty/too-short).
_CLEAN_TAKES_TO_FINISH = 1
_MAX_TAKES = 3
_MAX_ATTEMPTS = 5


class TrainingUI:
    """Where training progress is shown.

    The default implementation prints to the terminal (the classic --train
    experience); the native training window provides one that marshals each
    call onto the AppKit main thread. Training logic below reports through
    whichever it's handed and never touches print/input itself.
    """

    def show_prompt(self, text: str, hotkey_name: str) -> None:
        print(f'  Read aloud — hold [{hotkey_name}] and say: "{text}"')

    def nothing_heard(self) -> None:
        print("    (nothing heard — try again)")

    def heard_correctly(self) -> None:
        print("    ✓ heard it correctly")

    def correction_saved(self, heard_span: str, target: str) -> None:
        print(f'    ✗ heard "{heard_span}" — saved correction → "{target}"')

    def correction_conflict(self, heard_span: str, target: str) -> None:
        print(f'    ✗ heard "{heard_span}" — not saved (would conflict)')

    def session_started(self, name: str) -> None:
        print(f"\n{name} — read each line aloud.")

    def session_finished(self, name: str, saved: int) -> None:
        print(f"  Session complete: {saved} correction(s) learned.")


_PRINT_UI = TrainingUI()


class _TakeRecorder:
    """Records one hold-to-talk take at a time through a single, persistent
    hotkey listener.

    Training used to build and tear down a hotkey listener (a macOS
    CGEventTap) on every take; doing that dozens of times in a session
    destabilizes the CoreGraphics event system and crashes the app. The main
    dictation loop avoids this by keeping one listener alive for its whole
    run — this class does the same for training. Presses are ignored unless a
    take is actively in progress, so the hotkey does nothing between prompts
    or while a menu is waiting for typed input.

    Recorder start/stop runs on the control executor and the model on its
    dedicated worker thread — the same threading rules as normal dictation.
    Use as a context manager so the listener is always stopped on exit.
    """

    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        worker: ThreadPoolExecutor,
        control: ThreadPoolExecutor,
        play: Callable[[str], None],
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._worker = worker
        self._control = control
        self._play = play
        self._result: queue.Queue[str] = queue.Queue()
        self._active = False
        self._listener = HotkeyListener(self._on_hold_start, self._on_hold_end)

    def __enter__(self) -> "_TakeRecorder":
        self._listener.start()
        return self

    def __exit__(self, *exc) -> None:
        self._listener.stop()

    # Hotkey callbacks arrive on the event-tap thread and must return
    # instantly (even Popen for a sound is too slow) — hop to control.
    def _on_hold_start(self) -> None:
        if self._active:
            self._control.submit(self._start_and_beep)

    def _start_and_beep(self) -> None:
        self._recorder.start()
        self._play(config.SOUND_START)

    def _on_hold_end(self) -> None:
        if self._active:
            self._control.submit(self._stop_and_transcribe)

    def _stop_and_transcribe(self) -> None:
        audio = self._recorder.stop()
        self._play(config.SOUND_STOP)
        if self._recorder.duration_seconds(audio) < config.MIN_DURATION_SECONDS:
            self._result.put("")
            return
        self._result.put(
            self._worker.submit(self._transcriber.transcribe, audio).result()
        )

    def unblock(self) -> None:
        """Release a blocked record() with an empty take.

        The training window calls this when closing mid-session so the
        session thread stops waiting for a hotkey press that won't come.
        """
        self._result.put("")

    def record(self) -> str:
        """Wait for one hold-to-talk take; '' if nothing usable was captured."""
        while not self._result.empty():  # drop any stray press between takes
            try:
                self._result.get_nowait()
            except queue.Empty:
                break
        self._active = True
        try:
            while True:
                # Poll so Ctrl-C stays responsive while waiting for the hotkey.
                try:
                    return self._result.get(timeout=0.2)
                except queue.Empty:
                    continue
        finally:
            self._active = False


def _find_sublist(haystack: list[str], needle: list[str]) -> int | None:
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i : i + len(needle)] == needle:
            return i
    return None


def _capture(
    expected_text: str,
    targets: list[str],
    heard: str,
    profile: Profile,
    ui: TrainingUI = _PRINT_UI,
) -> tuple[bool, int]:
    """Compare a read-aloud take to what was expected.

    Aligns the expected and heard word streams; for every target word that
    landed in a misheard span, saves heard_span → target. Returns
    (clean, saved) where `clean` means every target was heard correctly.
    Corrections whose heard span is empty or entirely common words are
    skipped so an everyday word can't be rewritten by a stray take.
    """
    exp = normalize(expected_text).split()
    got = normalize(heard).split()
    opcodes = SequenceMatcher(None, exp, got).get_opcodes()

    saved = 0
    clean = True
    for target in targets:
        t_tokens = normalize(target).split()
        if not t_tokens:
            continue
        # Locate the target's run of tokens within the expected stream, then
        # gather the heard tokens aligned to that whole run — so a partly
        # misheard phrase is saved as one unit (avoids "Wispr Flow flow").
        start = _find_sublist(exp, t_tokens)
        if start is None:
            continue
        end = start + len(t_tokens)
        heard_tokens: list[str] = []
        for tag, i1, i2, j1, j2 in opcodes:
            lo, hi = max(i1, start), min(i2, end)
            if lo >= hi:
                continue
            if tag == "equal":  # 1:1 aligned — take just the target's slice
                heard_tokens.extend(got[j1 + (lo - i1) : j1 + (hi - i1)])
            elif tag == "replace":  # atomic block — take the whole heard side
                heard_tokens.extend(got[j1:j2])
            # 'delete': the target was dropped; nothing on the heard side
        heard_span = " ".join(heard_tokens).strip()
        if normalize(heard_span) == normalize(target):
            continue  # heard correctly
        clean = False
        if not heard_span or all(w in COMMON_WORDS for w in heard_span.split()):
            continue  # nothing usable, or would clobber an everyday word
        if profile.add_correction(heard_span, target):
            saved += 1
            ui.correction_saved(heard_span, target)
        else:
            ui.correction_conflict(heard_span, target)
    return clean, saved


def _train_prompt(
    text: str,
    targets: list[str],
    profile: Profile,
    record: Callable[[], str],
    hotkey_name: str,
    ui: TrainingUI = _PRINT_UI,
) -> int:
    """Run the takes for one prompt; returns how many corrections were saved."""
    clean = takes = saved = 0
    for _ in range(_MAX_ATTEMPTS):
        if takes >= _MAX_TAKES or clean >= _CLEAN_TAKES_TO_FINISH:
            break
        ui.show_prompt(text, hotkey_name)
        heard = record()
        if not heard:
            ui.nothing_heard()
            continue
        takes += 1
        was_clean, n = _capture(text, targets, heard, profile, ui)
        saved += n
        if was_clean:
            clean += 1
            ui.heard_correctly()
    return saved


def _run_session(
    session: dict,
    profile: Profile,
    record: Callable[[], str],
    hotkey_name: str,
    ui: TrainingUI = _PRINT_UI,
) -> int:
    ui.session_started(session["name"])
    saved = 0
    for prompt in session["prompts"]:
        saved += _train_prompt(
            prompt["text"], prompt["targets"], profile, record, hotkey_name, ui
        )
    profile.mark_session_done(session["name"])
    ui.session_finished(session["name"], saved)
    return saved


def _custom_loop(
    profile: Profile,
    record: Callable[[], str],
    hotkey_name: str,
) -> int:
    print("\nCustom — type a word or phrase you often dictate, then speak it.")
    saved = 0
    while True:
        try:
            term = input("\nWord or phrase (or 'done'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not term:
            continue
        if term.lower() == "done":
            break
        profile.add_word(term)
        saved += _train_prompt(term, [term], profile, record, hotkey_name)
    return saved


def _suggested_index(profile: Profile) -> int | None:
    """First session the profile hasn't finished, or None if all are done."""
    for i, session in enumerate(SESSIONS):
        if session["name"] not in profile.sessions_done:
            return i
    return None


def _session_menu(profile: Profile) -> str:
    """Print the menu and return a choice: an index string, 'c', or 'q'."""
    suggested = _suggested_index(profile)
    print("\nTraining sessions:\n")
    for i, session in enumerate(SESSIONS):
        mark = ""
        if session["name"] in profile.sessions_done:
            mark = "  ✓ done"
        elif i == suggested:
            mark = "  (suggested)"
        print(f"  {i + 1}. {session['name']}{mark}")
    print("\n  c. Custom — type your own words")
    print("  q. Finish training")
    default = str(suggested + 1) if suggested is not None else "q"
    try:
        choice = input(f"Choose [{default}]: ").strip().lower()
    except EOFError:
        return "q"
    return choice or default


def run_training(
    profile: Profile,
    recorder: Recorder,
    transcriber: Transcriber,
    worker: ThreadPoolExecutor,
    control: ThreadPoolExecutor,
    play: Callable[[str], None] = lambda sound: None,
) -> None:
    hotkey_name = config.hotkey_name()
    print(f"\nTraining profile '{profile.name}'.")
    print("Read each prompt aloud when asked — a quick way to teach Speakeasy your voice.")
    sessions = corrections = 0
    # One persistent listener for the whole training run (see _TakeRecorder).
    with _TakeRecorder(recorder, transcriber, worker, control, play) as taker:
        while True:
            choice = _session_menu(profile)
            if choice == "q":
                break
            if choice == "c":
                corrections += _custom_loop(profile, taker.record, hotkey_name)
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(SESSIONS):
                corrections += _run_session(
                    SESSIONS[int(choice) - 1], profile, taker.record, hotkey_name
                )
                sessions += 1
                continue
            print("  Enter a number from the list, 'c', or 'q'.")
    print(
        f"Training done: {sessions} session(s) completed, "
        f"{corrections} correction(s) learned. Saved to {profile.path}."
    )
