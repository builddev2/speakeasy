"""Microphone capture client for immediate hold-to-talk dictation.

The CoreAudio stream lives in a prewarmed helper process. If teardown wedges
inside the HAL, the engine watchdog can terminate that process and the next
keypress creates a fresh helper without restarting Speakeasy.
"""

import threading
import time
import json
from functools import lru_cache

import numpy as np

from . import config, settings
from .coreaudio import RecorderBusy, teardown
from .microphone_helper import MicrophoneHelper, MicrophoneHelperError

__all__ = ["Recorder", "RecorderBusy"]


@lru_cache(maxsize=1)
def _capture_device_class():
    import objc
    objc.loadBundle("AVFoundation", {},
                    bundle_path="/System/Library/Frameworks/AVFoundation.framework")
    return objc.lookUpClass("AVCaptureDevice")


def _permission_blocked():
    # A status query only: never request or reset the user's permission.
    return _capture_device_class().authorizationStatusForMediaType_("soun") in (1, 2)


class Recorder:
    def __init__(self) -> None:
        self._helper: MicrophoneHelper | None = None
        self._helper_lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._recording = False
        self._closed = False
        self.state = "ready"
        self.stream_dropped_frames = 0
        self.stream_delivery_complete = True

    @property
    def level(self) -> float:
        """RMS of the most recent audio chunk (0.0 when not recording)."""
        with self._helper_lock:
            helper = self._helper
        return helper.level if self._recording and helper is not None else 0.0

    @staticmethod
    def duration_seconds(audio: np.ndarray) -> float:
        """Length of a captured mono buffer in seconds."""
        return len(audio) / config.SAMPLE_RATE

    def _new_helper(self) -> MicrophoneHelper:
        return MicrophoneHelper()

    def prewarm(self) -> bool:
        # Shutdown must not terminate an unstarted helper that launches later.
        with self._launch_lock:
            return self._prewarm_locked()

    def _prewarm_locked(self) -> bool:
        """Open a stopped helper stream before the next keypress."""
        if teardown.in_flight or self._closed:
            return False
        if _permission_blocked():
            self.force_close()
            self.state = "permission_blocked"
            return False
        with self._helper_lock:
            if self._closed:
                return False
            if self._helper is not None:
                return True
            helper = self._new_helper()
            self._helper = helper
        try:
            helper.launch()
        except MicrophoneHelperError as error:
            self._discard_helper(helper)
            if error.code == "device_unavailable":
                self.state = "device_unavailable"
            return False
        if self._closed:
            self._discard_helper(helper)
            return False
        return True

    def recover(self, reason):
        """Called only by control; at most two launches, never reset HAL guards."""
        if self._closed:
            return False
        safe_reasons = {"helper_exit", "launch_failure", "stop_timeout", "overflow", "sleep_wake"}
        reason = reason if reason in safe_reasons else "helper_exit"
        started = time.monotonic()
        states = {"ready", "recording", "stopping", "restarting", "failed",
                  "permission_blocked", "device_unavailable"}
        previous_state = self.state if self.state in states else "failed"
        self.state = "restarting"
        self.force_close()
        attempts = 0
        for attempt in range(2):
            if self._closed or teardown.in_flight:
                break
            attempts = attempt + 1
            if self.prewarm():
                self.state = "ready"
                break
            if self.state in {"permission_blocked", "device_unavailable"}:
                break
            if attempt == 0:
                time.sleep(.05)
        else:
            self.state = "failed"
        if self.state == "restarting":
            self.state = "failed"
        print("MIC_RECOVERY " + json.dumps(dict(
            build_commit=settings.build_commit(), reason=reason,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            attempts=attempts, outcome=self.state,
            from_state=previous_state, via_state="restarting", to_state=self.state,
        )))
        return self.state == "ready" and not self._closed

    def shutdown(self):
        with self._launch_lock:
            with self._helper_lock:
                self._closed = True
            self.force_close()
            self.state = "failed"

    def start(self, *, chunk_queue=None) -> None:
        if self._recording:
            return
        if teardown.in_flight or self._closed:
            raise RecorderBusy()
        if not self.prewarm():
            raise RecorderBusy()
        with self._helper_lock:
            helper = self._helper
        if helper is None:
            raise RecorderBusy()
        try:
            helper.start(chunk_queue=chunk_queue)
        except MicrophoneHelperError as exc:
            self._discard_helper(helper)
            raise RecorderBusy() from exc
        self.stream_dropped_frames = 0
        self.stream_delivery_complete = chunk_queue is None
        self._recording = True
        self.state = "recording"

    def stop(self) -> np.ndarray:
        """Stop capture and return the recording as mono float32 samples."""
        if not self._recording:
            return np.empty(0, dtype=np.float32)
        self._recording = False
        self.state = "stopping"
        with self._helper_lock:
            helper = self._helper
        if helper is None:
            return np.empty(0, dtype=np.float32)
        try:
            audio = helper.stop()
            self.stream_dropped_frames = helper.stream_dropped_frames
            self.stream_delivery_complete = helper.stream_delivery_complete
            if not self.stream_delivery_complete:
                self._discard_helper(helper)
            self.state = "ready"
            return audio
        except MicrophoneHelperError:
            self.stream_dropped_frames = helper.stream_dropped_frames
            self.stream_delivery_complete = helper.stream_delivery_complete
            self._discard_helper(helper)
            raise

    def force_close(self) -> None:
        """Terminate the helper, including a CoreAudio call wedged inside it."""
        self._recording = False
        with self._helper_lock:
            helper, self._helper = self._helper, None
        if helper is not None:
            helper.terminate()

    def _discard_helper(self, helper: MicrophoneHelper) -> None:
        owned = False
        with self._helper_lock:
            if self._helper is helper:
                self._helper = None
                owned = True
        if owned:
            helper.terminate()
