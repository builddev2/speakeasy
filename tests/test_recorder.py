"""Dictation recorder helper lifecycle; no test opens a real microphone."""

import queue
import threading

import numpy as np
import pytest

from speakeasy import coreaudio
from speakeasy.microphone_helper import MicrophoneHelperError, _CaptureBuffer
from speakeasy.recorder import Recorder, RecorderBusy


@pytest.fixture(autouse=True)
def clean_guard():
    coreaudio.teardown.reset()
    yield
    coreaudio.teardown.reset()


class FakeHelper:
    def __init__(self, *, launch_error=False, start_error=False, stop_error=False):
        self.launch_error = launch_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.launched = False
        self.started = False
        self.terminated = False
        self.chunk_queue = None
        self.audio = np.ones(8, dtype=np.float32)
        self.level = 0.25
        self.stream_dropped_frames = 0
        self.stream_delivery_complete = True

    def launch(self):
        self.launched = True
        if self.launch_error:
            raise MicrophoneHelperError()

    def start(self, chunk_queue=None):
        self.started = True
        self.chunk_queue = chunk_queue
        self.stream_delivery_complete = chunk_queue is None
        if self.start_error:
            raise MicrophoneHelperError()

    def stop(self):
        if self.stop_error:
            raise MicrophoneHelperError()
        return self.audio

    def terminate(self):
        self.terminated = True


def _recorder_with(*helpers):
    recorder = Recorder()
    pending = iter(helpers)
    recorder._new_helper = lambda: next(pending)
    return recorder


def test_prewarm_reuses_helper_and_start_preserves_optional_chunk_queue():
    helper = FakeHelper()
    recorder = _recorder_with(helper)
    chunks = queue.Queue(maxsize=2)

    recorder.prewarm()
    recorder.prewarm()
    recorder.start(chunk_queue=chunks)

    assert helper.launched is True
    assert helper.started is True
    assert helper.chunk_queue is chunks
    assert recorder.level == 0.25
    assert recorder.stop().shape == (8,)
    assert recorder.level == 0.0


def test_failed_prewarm_is_terminated_and_start_is_busy():
    first = FakeHelper(launch_error=True)
    second = FakeHelper(launch_error=True)
    recorder = _recorder_with(first, second)

    recorder.prewarm()
    assert first.terminated is True

    with pytest.raises(RecorderBusy):
        recorder.start()
    assert second.terminated is True


def test_start_failure_discards_helper_and_next_take_recreates_it():
    failed = FakeHelper(start_error=True)
    recovered = FakeHelper()
    recorder = _recorder_with(failed, recovered)

    with pytest.raises(RecorderBusy):
        recorder.start()
    assert failed.terminated is True

    recorder.start()
    assert recovered.launched and recovered.started


def test_force_close_kills_wedged_helper_and_next_take_recreates_it():
    wedged = FakeHelper()
    recovered = FakeHelper()
    recorder = _recorder_with(wedged, recovered)
    recorder.start()

    recorder.force_close()

    assert wedged.terminated is True
    recorder.start()
    assert recovered.launched and recovered.started


def test_force_close_unblocks_stop_thread_before_recreating_helper():
    class BlockingHelper(FakeHelper):
        def __init__(self):
            super().__init__()
            self.stop_entered = threading.Event()
            self.killed = threading.Event()

        def stop(self):
            self.stop_entered.set()
            self.killed.wait(1.0)
            raise MicrophoneHelperError()

        def terminate(self):
            super().terminate()
            self.killed.set()

    wedged = BlockingHelper()
    recovered = FakeHelper()
    recorder = _recorder_with(wedged, recovered)
    recorder.start()
    stopped = threading.Event()
    errors = []

    def stop():
        try:
            recorder.stop()
        except MicrophoneHelperError as error:
            errors.append(error)
        finally:
            stopped.set()

    thread = threading.Thread(target=stop)
    thread.start()
    assert wedged.stop_entered.wait(0.2)

    recorder.force_close()

    assert stopped.wait(0.2)
    thread.join()
    assert len(errors) == 1
    recorder.start()
    assert recovered.started is True


def test_stop_failure_discards_helper():
    failed = FakeHelper(stop_error=True)
    recovered = FakeHelper()
    recorder = _recorder_with(failed, recovered)
    recorder.start()

    with pytest.raises(MicrophoneHelperError):
        recorder.stop()
    assert failed.terminated is True
    recorder.start()
    assert recovered.started is True


def test_incomplete_chunk_delivery_discards_helper_after_preserving_batch():
    helper = FakeHelper()
    helper.stream_delivery_complete = False
    recorder = _recorder_with(helper)
    recorder.start(chunk_queue=queue.Queue(maxsize=1))
    helper.stream_delivery_complete = False

    assert recorder.stop().shape == (8,)
    assert helper.terminated is True


def test_start_refuses_while_main_process_teardown_is_in_flight():
    helper = FakeHelper()
    recorder = _recorder_with(helper)

    with coreaudio.teardown.in_progress():
        with pytest.raises(RecorderBusy):
            recorder.start()
        recorder.prewarm()

    assert helper.launched is False


class Level:
    value = 0.0


def test_capture_callback_enqueues_without_blocking_and_batch_stays_complete():
    streamed = queue.Queue(maxsize=1)
    capture = _CaptureBuffer(Level(), streamed)
    capture.start(deliver_chunks=True)
    first = np.ones((4, 1), dtype=np.float32)
    second = np.full((4, 1), 2.0, dtype=np.float32)

    capture.callback(first, 4, None, None)
    capture.callback(second, 4, None, None)
    audio, capture_dropped, stream_dropped = capture.finish()

    assert audio.tolist() == [1.0] * 4 + [2.0] * 4
    assert capture_dropped == 0
    assert stream_dropped >= 4
    assert streamed.qsize() == 1  # preview may drop; final batch may not
    assert Level.value == 0.0


def test_capture_callback_drops_immediately_when_realtime_queue_is_full():
    class FullQueue:
        def put_nowait(self, value):
            raise queue.Full

    capture = _CaptureBuffer(Level(), queue.Queue(maxsize=1))
    capture._session = (FullQueue(), [], None)
    capture.callback(np.ones((7, 1), dtype=np.float32), 7, None, None)

    assert capture.capture_dropped_frames == 7
    capture._session = None


def test_duration_and_idle_stop_compatibility():
    recorder = Recorder()
    assert recorder.stop().size == 0
    assert recorder.duration_seconds(np.zeros(16000, dtype=np.float32)) == 1.0
