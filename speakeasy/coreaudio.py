"""Main-process serialization of meeting teardown against microphone open.

Opening an input stream while *any* prior stop/abort is still executing inside
the CoreAudio HAL deadlocks both calls: one thread sits in
`AudioOutputUnitStop`, the other in `Pa_OpenStream`, and both wait on
`HALB_Mutex::Lock()`. The process stays alive with the mic and the hotkey dead.

Meeting stop still runs in the main process. Its watchdog can move on while the
abandoned teardown remains in the HAL, so later opens must consult this guard.
Immediate dictation owns its stream in a killable helper process and checks the
guard before launching; a wedged dictation teardown dies with that helper.

While a main-process teardown is in flight, opening raises `RecorderBusy` and
the caller drops the take or meeting rather than freezing the pipeline.

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

    Raised by `Recorder.start()` and `MeetingRecorder.start()`. It also covers
    failure to launch or start the isolated dictation helper.
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
