"""Microphone capture client for immediate hold-to-talk dictation.

The CoreAudio stream lives in a prewarmed helper process. If teardown wedges
inside the HAL, the engine watchdog can terminate that process and the next
keypress creates a fresh helper without restarting Speakeasy.
"""

import threading

import numpy as np

from . import config
from .coreaudio import RecorderBusy, teardown
from .microphone_helper import MicrophoneHelper, MicrophoneHelperError

__all__ = ["Recorder", "RecorderBusy"]


class Recorder:
    def __init__(self) -> None:
        self._helper: MicrophoneHelper | None = None
        self._helper_lock = threading.Lock()
        self._recording = False
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

    def prewarm(self) -> None:
        """Open a stopped helper stream before the next keypress."""
        if teardown.in_flight:
            return
        with self._helper_lock:
            if self._helper is not None:
                return
            helper = self._new_helper()
            self._helper = helper
        try:
            helper.launch()
        except MicrophoneHelperError:
            self._discard_helper(helper)

    def start(self, *, chunk_queue=None) -> None:
        if self._recording:
            return
        if teardown.in_flight:
            raise RecorderBusy()
        self.prewarm()
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

    def stop(self) -> np.ndarray:
        """Stop capture and return the recording as mono float32 samples."""
        if not self._recording:
            return np.empty(0, dtype=np.float32)
        self._recording = False
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
