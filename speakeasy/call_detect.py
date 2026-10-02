"""Is another app using the microphone? One boolean for the record banner.
An app counts only from when it starts using the mic while Speakeasy watches.

PIDs stay inside this module: they are never logged, stored or shown. The
probe runs on its own daemon thread because each answer costs a helper
process launch (up to 1 s); it never opens an audio stream and never
touches the model, the executors or UI objects.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Callable

from . import system_audio

POLL_SECONDS = 5.0


def own_process_ids() -> set[int]:
    """Speakeasy and its direct children (dictation helper, system-audio
    helper, diarization child): their mic use is ours, not another app's."""
    own = os.getpid()
    ids = {own}
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-P", str(own)],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ids
    ids.update(int(token) for token in result.stdout.split() if token.isdigit())
    return ids


class MicWatch:
    """Is another app on a call? An app counts only from when it starts using
    the mic while Speakeasy watches. Apps already using it at the first known
    answer (GeForce NOW keeps input running while idle), and apps behind a
    waved-off call offer, are ignored until they release the mic. Called on
    the probe thread; the PIDs never leave this object."""

    def __init__(self, input_ids=None, own_ids=None) -> None:
        self._input_ids = input_ids or system_audio.input_process_ids
        self._own_ids = own_ids or own_process_ids
        self._ignored: set[int] | None = None   # None until the first known answer
        self._ignore_requested = False

    def ignore_current(self) -> None:
        """Main thread: ignore the apps using the mic now. A plain flag that
        the probe thread applies on its next answer."""
        self._ignore_requested = True

    def __call__(self) -> bool | None:
        active = self._input_ids()
        if active is None:
            return None
        others = active - self._own_ids()
        requested, self._ignore_requested = self._ignore_requested, False
        if self._ignored is None or requested:
            self._ignored = (self._ignored or set()) | others
        self._ignored &= others   # released the mic: its next use counts
        return bool(others - self._ignored)


class CallProbe:
    """Every `interval` seconds while `wanted()` is true, ask `probe` and
    hand a known answer to `on_result`, which must hop to the main thread
    itself. `wanted` is read from this thread: keep it a plain flag read."""

    def __init__(self, on_result: Callable[[bool], None], wanted: Callable[[], bool],
                 probe: Callable[[], bool | None] | None = None,
                 interval: float = POLL_SECONDS) -> None:
        self._on_result = on_result
        self._wanted = wanted
        self.watch = MicWatch() if probe is None else None
        self._probe = probe or self.watch
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="call-probe", daemon=True)

    def ignore_current(self) -> None:
        if self.watch is not None:
            self.watch.ignore_current()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        # No join: at most one helper run (1 s timeout) is in flight, and the
        # thread is a daemon, so quit never waits on it.
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not self._wanted():
                continue
            result = self._probe()
            if result is not None and not self._stop.is_set():
                self._on_result(result)
