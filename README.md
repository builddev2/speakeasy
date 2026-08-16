# Speakeasy

Local, private Wispr Flow clone for macOS. A menu-bar app: hold
**Right Command (⌘)** anywhere, speak, release — the transcribed text is pasted
at your cursor. Speech-to-text runs entirely on-device (NVIDIA Parakeet on Apple
MLX); nothing ever leaves your Mac.

Speakeasy lives in the menu bar as a small skull icon — it turns into a mic
while recording and a waveform while transcribing. Click its status item to
see the current state, switch profiles, train, record a meeting, or quit.
Speakeasy also has a normal Dock icon; clicking it opens a small window with
the same core actions, as a fallback for when the status item is hidden by
menu-bar overflow (common with many menu-bar apps installed) or just hard to
spot — the status item stays the primary, full-featured interface.

It can also transcribe whole **meetings** (up to three hours): start a
recording from the menu bar and, when you end it, Speakeasy produces a
speaker-labelled transcript on-device — see
[Meeting transcription](#meeting-transcription). Only the transcript is kept;
the audio is deleted as soon as processing finishes.

## Install

Speakeasy ships as a standalone **Speakeasy.app** — once built it needs no
Terminal, no Python, and no network. Building it does need the source checkout
and a one-time dev environment (see [Development](#development-run-from-source)
for the `.venv` setup).

### 1. Build the app

Needs Node.js/npm on `PATH` (one-time build prerequisite — `build_app.sh`
builds the web UI it embeds and never fetches anything at runtime):

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && scripts/build_app.sh
```

This produces `dist/Speakeasy.app` (~2.6 GB — it embeds the full 2.3 GB speech
model, so the app is fully offline the first time you open it). To build **and**
install it into `/Applications` in one step:

```bash
scripts/build_app.sh --install
```

`--install` updates the bundle **in place** (it rsyncs the new build over the
existing `/Applications/Speakeasy.app`, quitting a running instance first)
rather than deleting and recreating it. That matters for permissions: macOS
keys the Accessibility / Input Monitoring grants on the app's code signature,
and replacing the whole bundle can drop the existing grant and force you to
re-approve. Overwriting the same bundle keeps it — so with a stable signing
identity (below), later `--install` rebuilds don't re-prompt.

Without `--install` the build only writes `dist/`, and you can install it
yourself; prefer overwriting in place over `mv` for the same reason:

```bash
rsync -a --delete dist/Speakeasy.app/ /Applications/Speakeasy.app/
```

> **Signing (recommended, one time).** By default the build ad-hoc signs the
> app, and macOS treats *every rebuild* as a brand-new app — you'd have to
> re-grant all three permissions each time. To keep your grants across
> rebuilds, create a self-signed **code-signing** certificate named
> `Speakeasy Dev` once: open **Keychain Access → Certificate Assistant →
> Create a Certificate…**, name it `Speakeasy Dev`, set *Certificate Type* to
> **Code Signing**, and create it. `build_app.sh` picks it up automatically
> (override with `SIGN_ID=…`).

### 2. Grant macOS permissions (one time)

Launch Speakeasy (from Spotlight or `/Applications`). It appears in the menu
bar and, on first run, guides you through the three permissions needed for
dictation **to Speakeasy itself** in **System Settings → Privacy & Security**:

- **Microphone** — to record your voice (prompts automatically on first recording)
- **Accessibility** — to paste the transcribed text (simulated ⌘V)
- **Input Monitoring** — to detect the Right Command hotkey

Meeting system-audio capture has one additional, optional grant on macOS
14.2+: **Screen & System Audio Recording**. macOS prompts the first time you
begin a meeting. Speakeasy captures audio only—never screen pixels. If you
deny or later revoke it, the meeting continues visibly in microphone-only
mode; with headphones, remote participants will not be captured in that mode.

After enabling **Accessibility** and **Input Monitoring**, quit Speakeasy from
its menu-bar item and reopen it for the changes to take effect.

> **Prompted again after a reinstall?** If Speakeasy asks for Accessibility /
> Input Monitoring even though you'd already granted them — and the Right ⌘
> hotkey does nothing — the old grant is stale (it pointed at a bundle that was
> replaced rather than updated in place). Clear the two entries and re-grant:
>
> ```bash
> tccutil reset ListenEvent com.jasonchiu.speakeasy
> tccutil reset Accessibility com.jasonchiu.speakeasy
> ```
>
> Then relaunch Speakeasy and approve both when prompted. Building with
> `scripts/build_app.sh --install` avoids this going forward.

### Uninstall

```bash
scripts/uninstall.sh            # remove the app + log; keep your profiles/settings
scripts/uninstall.sh --purge    # also remove profiles, settings, and the cached model
```

It quits Speakeasy, deletes the `.app` (from `/Applications`, `~/Applications`,
or `dist/`) and `~/Library/Logs/Speakeasy.log`, and — by default — keeps your
data in `~/Library/Application Support/Speakeasy` so a reinstall resumes where
you left off. It can't revoke the macOS permission grants or delete the
`Speakeasy Dev` cert; it prints where to do both.

## Using it

When Speakeasy is running, its menu-bar item shows the current state (`Ready`,
`Recording…`, `Transcribing…`). Hold **Right Command**, speak, and release: a
pop sound and dancing rainbow waveform bars (bottom-center of the screen) mark
recording, a bottle sound marks stop, and the text appears at your cursor within
about a second.

Click the menu-bar item for:

- **Profile** — switch the active profile, pick **Guest (no corrections)**, or
  create a **New Profile…**
- **Train Profile…** — open the training window (see below)
- **Begin Meeting** / **End Meeting** — record and transcribe a meeting (see
  [Meeting transcription](#meeting-transcription))
- **Meetings…** — browse, copy, export, rename, or delete saved transcripts
- **Quit Speakeasy** (⌘Q)

## Profiles: teach it your words

The speech model is fixed, so it can mishear personal vocabulary — names,
jargon, technical terms ("Claude" might come out as "clod"). A **profile**
fixes that two ways. It learns **corrections**: you train it once on your
words, and every future dictation rewrites those exact misrecognitions
automatically. It also keeps a personal **vocabulary** — any word you add gets
snapped onto the near-miss spellings the model emits for it ("kubernetis" →
"Kubernetes"), but only when a word both *sounds* like and closely *spells* the
vocabulary entry, so ordinary words are left untouched. Both run as cheap text
passes, so they add no perceptible latency — dictation stays at the usual
~1 second.

Switch or create profiles from the menu-bar **Profile** submenu. **Guest**
dictates without corrections. The active profile is shown with a checkmark.

### Training

Choose **Train Profile…** from the menu bar to open the training window.
Instead of asking you to think up words, Speakeasy suggests short sessions to
read aloud — everyday phrases first, then the words the model most often
mishears (tech jargon, names, numbers, tricky words):

- **Everyday phrases** (suggested first)
- **Tech & jargon**
- **Names & proper nouns**
- **Numbers & units**
- **Tricky words & homophones**
- **Custom** — type your own words for personal names the built-in content
  can't know

For each line, hold Right Command and read it aloud; when a target word is
misheard, the correction is saved to the profile. Finished sessions are marked
done and remembered, so the window always points you at what's left. All
content ships offline; progress is saved as you go.

### Details

- Profiles are plain JSON in `~/Library/Application Support/Speakeasy/profiles/`,
  one file per profile, and hand-editable — add corrections directly as
  `"heard": "intended"` pairs. Corrections match whole words,
  case-insensitively; longer phrases win over shorter ones.
- The `vocabulary` list (words you add during **Custom** training, or by hand)
  applies the same way to both dictations and meeting transcripts: a near-miss
  only snaps to a vocabulary word when it clears both a phonetic and a
  close-spelling check, so a word you actually use isn't rewritten to a
  look-alike.
- Training refuses corrections that would rewrite another word the profile
  knows, so real words can't be mapped away by a bad take.
- Everything stays on-device: profiles are local files; nothing is uploaded.

## Meeting transcription

Click **Begin Meeting** in the menu bar to start recording. On macOS 14.2+,
Speakeasy records your microphone and outgoing system audio as separate
temporary tracks, so remote participants are captured while you wear
headphones. The Dock window defaults to **All system audio** and can instead
target one currently eligible audio process. Application names and PIDs are
used only in the live selector; they are never logged or saved. Browser choices
capture the selected browser process, not an individual tab. The status
explicitly says **microphone + system audio**, **microphone + selected
application**, or **microphone only**. Meetings capture up to three hours;
the menu shows the elapsed time. Click **End Meeting** when you're done,
and Speakeasy processes the recording entirely on-device:

1. each available track is transcribed in chunks (progress shows in the menu),
2. microphone speech is labelled **You**, while only the clean system track is
   separated by the local speaker-diarization model, and
3. both tracks are shifted by their real first-buffer offsets, merged on one
   timeline, and saved with remote voices as **Speaker 1**, **Speaker 2**, …
   (or an enrolled name when a confident match exists).

A 2-hour meeting takes several minutes to process; **Cancel Processing** in
the menu discards it. When it finishes, the transcript appears under
**Meetings…**, where you can:

- **Copy** it to the clipboard and paste it into any notepad,
- **Export…** it as a `.txt` or `.md` file,
- **Rename…** or **Delete** it.

While a meeting records, hold-to-talk dictation is off (both would fight over
the mic and the model); it re-arms the moment processing starts. The active
profile's corrections are applied to both tracks. The speaker count defaults to
**one remote speaker** for a one-on-one call and excludes you. Clear it for
automatic group-meeting detection or enter the known number of remote speakers.
In microphone-only fallback Speakeasy adds you to that count because source
separation is unavailable and every voice is audible on the mixed mic track.

**Privacy: the audio itself is never kept.** During the meeting both tracks
spool to temporary files, which are deleted as soon as the transcript is saved — and
also on cancel, on failure, on quit, and (if the app ever crashes mid-meeting)
swept at the next launch. Transcripts are plain JSON in
`~/Library/Application Support/Speakeasy/meetings/`. Everything — recording,
transcription, speaker identification — runs offline; nothing leaves your Mac.

**Fallback and speaker-mode limitation.** macOS 14.0–14.1, a denied/revoked
system-audio grant, or an unavailable capture helper falls back safely to the
original mic-only meeting pipeline. In that mode remote participants are only
captured through speakers, and every voice audible on the mic is diarized;
Speakeasy does not falsely label the whole mixed track as **You**. With system
capture active, speaker playback can still bleed into the microphone and
appear twice. Speakeasy preserves both segments rather than risk deleting a
real repeated phrase or overlapping speech; headphones provide the cleanest
**You** versus remote-speaker separation. A global system tap also includes
unrelated notification or music audio played during the meeting. Selected-app
capture never silently widens to the global tap: if that process exits,
restarts, or cannot be resolved, the meeting reports the selected application
as unavailable and continues microphone-only until you reselect it next time.

During recording, the Dock and menu expose privacy-safe capture health: whether
each track has produced a first buffer, whether system audio has a nonzero
signal, dropped-frame counts, writer lag/failure, helper exit, and the explicit
fallback outcome. Saved meetings retain only that fixed metadata schema plus
the transcript and timing offsets — never application/device names, PIDs,
window titles, audio samples, or other provenance that could reveal content.

## Development (run from source)

You don't need the frozen app to develop — run the package directly from a
virtualenv.

### 1. Install dependencies (one time)

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The first run downloads the speech model (~2.3 GB) to `~/.cache/huggingface`;
later runs load it instantly from that cache, and `build_app.sh` copies it out
of there into the app bundle. The meetings feature needs two small
speaker-diarization models (~44 MB total) fetched once, checksum-verified,
into `models/diarization/`:

```bash
scripts/fetch_diarization_models.sh
```

These are the only download steps — both happen at dev/build time, never at
runtime.

Dependencies are pinned to exact, tested versions in `requirements.txt` so a
reinstall can't silently pull a broken or tampered release. To reproduce the
complete tested environment — every transitive package too — install from the
full snapshot instead: `.venv/bin/pip install -r requirements.lock.txt`. Build
tooling (pytest, pyinstaller) lives in `requirements-dev.txt`.

### 2. Run it

The dock, meetings, and training windows are WKWebViews hosting a built
frontend — build it once (needs Node.js/npm; rebuild after editing anything
under `frontend/`):

```bash
npm --prefix frontend ci && npm --prefix frontend run build
scripts/build_system_audio_helper.sh
```

```bash
.venv/bin/python -m speakeasy          # menu-bar app (same as the .app)
.venv/bin/python -m speakeasy --cli    # terminal front end (profile picker in the terminal)
.venv/bin/python -m speakeasy --train  # guided training in the terminal (implies --cli)
```

`--profile NAME` skips the picker (handy for a launch alias). When run from
Terminal, dictation permissions attach to **Terminal** (or iTerm), not to the
`.app` — grant them there too if you develop from source. The system-audio
helper embeds its own usage description for source runs.

### Tests

```bash
.venv/bin/python -m pytest             # pure-logic units: profiles, training alignment, mel shim,
                                        # meeting store/alignment/recorder/engine flow
```

### Accuracy evaluation

Run the fully offline evaluator against consented, locally stored fixtures:

```bash
.venv/bin/python -m speakeasy.evaluate sample.wav --reference reference.json
.venv/bin/python -m speakeasy.evaluate sample.wav --text reference.txt --rttm reference.rttm
```

It reports WER, diarization error, speaker-count error, attributed WER,
real-time factor, elapsed time, and peak resident memory. JSON references use
`{"text": "...", "segments": [{"speaker": "Alice", "start": 0.0,
"end": 1.2, "text": "..."}]}`. Audio fixtures are never bundled by default;
only add recordings whose participants explicitly consented to repository use.

To compare an already-downloaded MLX model without changing production config:

```bash
.venv/bin/python -m speakeasy.evaluate sample.wav --reference reference.json \
  --model-id mlx-community/parakeet-tdt-0.6b-v3
```

### Speaker enrollment and vocabulary import

Speaker enrollment stores only a local TitaNet embedding; the source WAV is
read but never copied. A 16 kHz mono WAV is required:

```bash
.venv/bin/python -m speakeasy --enroll-voice Alice alice-enrollment.wav
.venv/bin/python -m speakeasy --list-voices
.venv/bin/python -m speakeasy --delete-voice Alice
.venv/bin/python -m speakeasy --import-vocabulary Jason terms.txt
```

The dock meeting panel can optionally use the enrolled profiles and an expected
speaker count. Speaker labels in saved meetings can be clicked and corrected;
the correction can apply to one segment or every matching segment. Identification
is deliberately conservative: weak, ambiguous, or duplicate matches keep their
anonymous `Speaker N` label.

## Waveform indicator

While you hold the hotkey, seven glassy pastel-rainbow bars float at the
bottom-center of your screen and dance with your voice level — no box or
border, just the bars. After you release, they ripple gently while the text
transcribes, then fade out when it pastes. The indicator never steals focus
and is click-through.

Set `OVERLAY_ENABLED = False` in `speakeasy/config.py` to turn it off
entirely (sounds and status still work).

## Configuration

Edit `speakeasy/config.py` to change:

- `HOTKEY` — the push-to-talk key (currently Right Command / `cmd_r`)
- `MODEL_ID` — the speech model
- `SOUNDS_ENABLED`, `SOUND_START`, `SOUND_STOP` — audio feedback
- `SAMPLE_RATE`, `MIN_DURATION_SECONDS`, `RECORDER_STOP_TIMEOUT_SECONDS`,
  `CLIPBOARD_SETTLE_SECONDS`, `PASTE_SETTLE_SECONDS` — capture/paste timing
- `DICTATION_TRIM_ENABLED`, `TRIM_*` — trim leading/trailing silence from a
  dictation before transcription (a touch faster, steadier accuracy)
- `FUZZY_VOCAB_ENABLED`, `FUZZY_MIN_RATIO`, `FUZZY_MIN_TOKEN_LEN` — vocabulary
  near-miss snapping sensitivity
- `OVERLAY_*` — waveform indicator on/off, bar count/size, position, translucency
- `MEETING_CHUNK_SECONDS`, `MEETING_OVERLAP_SECONDS`, `MEETING_MAX_SECONDS` —
  chunked transcription and the spool size cap
- `DIARIZATION_THRESHOLD`, `DIARIZATION_MIN_ON`, `DIARIZATION_MIN_OFF`,
  `DIARIZATION_SPLIT_MIN_SECONDS` — speaker-clustering and boundary sensitivity
- `SPEAKER_MATCH_THRESHOLD`, `SPEAKER_MATCH_MARGIN` — conservative enrolled
  voice matching

## How it works

```
hold Right Command ─► record mic (16 kHz mono) + rainbow bars dance with voice
release            ─► transcribe locally (Parakeet on Apple MLX / Metal)
                   ─► paste text at cursor (clipboard + simulated ⌘V)
                   ─► restore your previous clipboard, bars fade out
```

```
Begin Meeting ─► mic + system audio spool separately (dictation disabled)
End Meeting   ─► transcribe both tracks; mic = You; diarize system track
                  ─► apply first-buffer offsets ─► chronological merge
                  ─► save transcript ─► delete both temporary WAVs
                  ─► dictation hotkey re-armed
```

- **`hotkey.py`** — global hold-to-talk listener built on a raw Quartz
  `CGEventTap` (no third-party input library). A listen-only tap makes no Text
  Input Source calls, and pausing around the synthesized paste is a
  `CGEventTapEnable` flip rather than a listener teardown
- **`recorder.py`** — microphone capture (sounddevice) + live RMS level for
  the waveform bars. The CoreAudio stream is opened once at startup and kept
  across recordings, so pressing the hotkey starts capture instantly instead
  of paying a ~100 ms stream open that could clip your first syllable (the
  mic-in-use indicator still only shows while you're actually recording). A
  stop that wedges inside CoreAudio is timed out and abandoned by a watchdog;
  while its teardown is still unwinding, opening a second stream would deadlock
  both on the HAL mutex, so a stuck mic degrades to "try again" / relaunch
  instead of freezing the whole hotkey pipeline
- **`coreaudio.py`** — the process-wide guard that makes the above safe. The
  HAL mutex is per process/device, and dictation and meetings each own a
  separate input stream on it, so a wedged teardown on *either* recorder blocks
  an open on *both*. Both mark their teardown here and both check it before
  opening; while one is in flight, a dictation take or a Begin Meeting is
  refused (`RecorderBusy`) rather than deadlocked. Capture self-recovers the
  moment the wedged call returns
- **`transcriber.py`** — Parakeet MLX model; loaded and run on one dedicated
  worker thread, because MLX pins its GPU arrays to their creating thread.
  Audio is fed to the model in-memory (log-mel + generate), skipping the
  temp-WAV file and ffmpeg decode of the library's path-based API. Meeting ASR
  uses `transcribe_long_wav()` to read one overlap chunk at a time directly
  from each spool; its array counterpart `transcribe_long()` shares the exact
  same token-merge loop. Tracks are transcribed sequentially, and only the
  track needed for diarization is loaded fully afterwards, keeping long-meeting
  peak memory bounded without changing timestamps or cancellation behavior
- **`preprocess.py`** — trims leading/trailing silence from a dictation before
  it reaches the model: fewer mel frames (a touch faster) and a tighter
  per-feature normalization, so a short phrase buried in silence transcribes
  more reliably. Meetings skip it — trimming would shift transcript timestamps
  out of sync with the diarization turns they're aligned against
- **`_mel_shim.py`** — a pure-numpy log-mel filterbank that stands in for
  librosa, so the freeze-fragile numba/llvmlite/scipy tree is dropped from the
  bundle entirely
- **`profiles.py`** — per-user profiles: learned heard→intended corrections
  plus a personal vocabulary, stored as JSON and applied to each transcript.
  Corrections are one pre-compiled regex pass; the vocabulary adds a second
  pass that snaps near-miss words onto known terms only when they clear both a
  phonetic (`phonetics.py`) and a close-spelling gate — still microseconds
- **`phonetics.py`** — a small vendored Soundex-style phonetic key that gates
  the vocabulary fuzzy match (no dependency, fully offline)
- **`training.py`** — the guided training content and alignment: you read a
  line aloud, and the model's actual misrecognitions are saved to the profile
  as corrections
- **`ui/menubar.py`** — the `NSStatusItem`, its menu, and the app delegate that
  wires the engine to the AppKit run loop. The status glyph is a skull at rest
  (custom template image; macOS has no `skull` SF Symbol) that swaps to
  `mic.fill`/`waveform` while active
- **`ui/webwindow.py` + `ui/webbridge.py`** — the common transparent WKWebView
  host and pure-Python bridge for the Dock, meetings, and training pages. Pages
  load only built local assets; WebKit calls remain main-thread-only, and the
  bridge queues early state pushes until navigation completes
- **`ui/main_window.py`, `ui/meetings_window.py`, `ui/training_window.py`** —
  thin native controllers around those web-rendered pages. They expose live
  engine/capture state, meeting actions and speaker relabeling, and the guided
  training lifecycle while preserving the existing executor boundaries
- **`meeting_recorder.py`** — dual-track coordination plus long-form mic
  capture: an int16 stream whose
  audio callback only enqueues bytes, drained to a spool WAV by a dedicated
  writer thread, so RAM stays flat regardless of meeting length. Spools live
  in `settings.spool_dir()`, are deleted on every exit path, and are swept
  clean at the next launch if the app ever crashes mid-meeting. Its stop runs
  under the same watchdog as dictation's, and it shares the `coreaudio.py`
  teardown guard — a meeting whose stop wedges can't deadlock the dictation
  stream that re-arms behind it
- **`system_audio.py` + `native/SystemAudioCapture.swift`** — macOS 14.2+
  Core Audio process-tap capture through a small bundled helper process. Its
  realtime callback only copies into a bounded queue; a separate writer queue
  downmixes/resamples to 16 kHz mono PCM16 and writes the temporary system WAV.
  The default global tap can be narrowed to one transient Core Audio process
  ID; only processes reported by Core Audio appear in the selector, and
  disappearance/restart is explicit and never falls back to global. Bounded,
  non-content events feed the live capture-health display. Process
  isolation keeps a wedged system-tap teardown from holding Speakeasy's
  microphone HAL lock. The helper is feature-gated, so the app's macOS 14.0
  minimum remains unchanged and unsupported/denied starts fall back visibly
  to mic-only
- **`meetings.py`** — the meeting store (JSON per meeting, atomic saves like
  `profiles.py`), `.txt`/`.md` rendering, and the pure `align_speakers()`
  algorithm that maps diarization turns onto transcribed sentences
- **`diarizer.py`** — sherpa-onnx speaker diarization (CPU/onnxruntime, no
  MLX thread-pinning rule); lazily constructed on the first meeting from the
  two bundled ONNX models
- **`ui/overlay.py`** — the waveform indicator: a borderless, click-through,
  non-activating AppKit panel animated at 30 fps only while visible
- **`ui/permissions.py`** — first-run permissions guidance and the
  Accessibility / Input Monitoring status checks (via `AXIsProcessTrusted`)
- **`injector.py`** — clipboard save → set text → ⌘V → restore, all via
  NSPasteboard in-process (no pbcopy/pbpaste subprocesses), with ⌘V posted as
  raw Quartz keyboard events. The clipboard is saved while transcription runs,
  and restored after. The hotkey's event tap is disabled for the moment of the
  ⌘V and re-enabled right after, so the listener can't duplicate the paste
- **`cli.py`** — the terminal front end (`--cli` / `--train`): the profile
  picker and guided training as a text UI
- **`__main__.py`** — the entry point; the menu-bar app is the default and the
  AppKit run loop owns the main thread. Threading rules: transcription runs on
  its own worker thread, and recorder start/stop runs on a control thread —
  never on the hotkey event-tap thread, where stopping CoreAudio deadlocks
  against the HAL mutex
- **`launcher.py`** — the frozen-app entry point: redirects stdout/stderr to
  `~/Library/Logs/Speakeasy.log` (a windowed app has none) before starting

## Notes

- Text is inserted via clipboard + simulated ⌘V; your previous clipboard **text**
  is restored afterwards (non-text contents like images are not).
- Recordings shorter than 0.3 s are ignored as accidental taps.
- The global hotkey is briefly inactive (a few hundredths of a second) while
  the ⌘V is synthesized, so the text pastes exactly once; a hotkey press in
  that instant — right after you release — would be missed, but it's far too
  short to hit in practice.
- User data (profiles, meeting transcripts, logs) lives under
  `~/Library/Application Support/Speakeasy/` and `~/Library/Logs/Speakeasy.log`.
  Meeting audio itself is never part of that persisted data — only the
  transcript is kept (see [Meeting transcription](#meeting-transcription)).
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
