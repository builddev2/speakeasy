"""Process-wide serialization of CoreAudio stream teardown against stream open.

Opening an input stream while *any* prior stop/abort is still executing inside
the CoreAudio HAL deadlocks both calls: one thread sits in
`AudioOutputUnitStop`, the other in `Pa_OpenStream`, and both wait on
`HALB_Mutex::Lock()`. The process stays alive with the mic and the hotkey dead.

The engine's stop watchdogs make this reachable by design: when a stop doesn't
return in time they abandon it and move on, so a teardown can still be unwinding
in the HAL long after the code that started it has given up on it.

That mutex is per-process/per-device, so the guard has to be too — a per-recorder
flag would still let a wedged `MeetingRecorder.stop()` deadlock against a
`Recorder.start()` (different object, same HAL). Both recorders therefore mark
their teardown here, and both consult it before opening; while a teardown is in
flight, opening raises `RecorderBusy` and the caller drops the take rather than
freezing the pipeline.

It is a counter, not a flag: a wedged teardown can overlap a later clean one, and
the clean one's exit must not clear the wedge. A teardown that never returns
leaves the count raised forever — deliberately. The mic then stays unavailable
until the HAL call finally unwinds (the count drops, capture self-recovers) or
until relaunch, but the app never hangs.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class RecorderBusy(Exception):
    """An open was refused: a CoreAudio teardown is still unwinding in the HAL.

    Raised by `Recorder.start()` and `MeetingRecorder.start()`. Opening anyway
    would deadlock on the HAL mutex, so the engine drops that take and stays
    idle, keeping the hotkey live.
    """


class _Teardown:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    @property
    def in_flight(self) -> bool:
        """True while at least one CoreAudio teardown has not returned."""
        with self._lock:
            return self._count > 0

    @contextmanager
    def in_progress(self) -> Iterator[None]:
        """Mark a CoreAudio teardown in flight for the duration of the block.

        If the wrapped call wedges, the block never finishes and the count stays
        raised — which is exactly what keeps a concurrent open from deadlocking
        against it.
        """
        with self._lock:
            self._count += 1
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1

    def reset(self) -> None:
        """Tests only: clear a simulated wedge so it can't leak between them."""
        with self._lock:
            self._count = 0


teardown = _Teardown()
