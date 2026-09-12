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
    first = np.ones((80_000, 1), dtype=np.float32)
    second = np.full((80_000, 1), 2.0, dtype=np.float32)

    capture.callback(first, 80_000, None, None)
    capture.callback(second, 80_000, None, None)
    audio, capture_dropped, stream_dropped = capture.finish()

    assert np.array_equal(audio[:80_000], np.ones(80_000))
    assert np.array_equal(audio[80_000:], np.full(80_000, 2.0))
    assert capture_dropped == 0
    assert stream_dropped >= 80_000
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


def test_recovery_prewarms_fresh_helper_without_another_take():
    failed, healthy = FakeHelper(), FakeHelper()
    recorder = _recorder_with(failed, healthy)
    recorder.prewarm()
    assert recorder.recover("helper_exit")
    assert failed.terminated
    assert healthy.launched
    assert recorder.state == "ready"


def test_recovery_is_finite_and_shutdown_never_reopens():
    helpers = [FakeHelper(launch_error=True) for _ in range(2)]
    recorder = _recorder_with(*helpers)
    assert not recorder.recover("launch_failure")
    assert all(helper.terminated for helper in helpers)
    assert recorder.state == "failed"
    recorder.shutdown()
    assert not recorder.recover("helper_exit")


def test_1000_recovery_cycles_keep_exact_helper_ownership():
    recorder = Recorder()
    helpers = []
    def create():
        helper = FakeHelper()
        helpers.append(helper)
        return helper
    recorder._new_helper = create
    for cycle in range(1000):
        recorder.start()
        assert recorder.stop().shape == (8,)
        assert recorder.recover("helper_exit")
        assert sum(not h.terminated for h in helpers) == 1
    recorder.shutdown()
    assert all(h.terminated for h in helpers)


def test_shutdown_during_prewarm_cannot_leave_a_live_helper():
    class SlowLaunch(FakeHelper):
        entered = threading.Event()
        release = threading.Event()
        def launch(self):
            self.entered.set()
            assert self.release.wait(1)
            super().launch()
            self.live = True
        def terminate(self):
            if self.terminated:
                return
            super().terminate()
            self.live = False
    helper = SlowLaunch()
    recorder = _recorder_with(helper)
    prewarm = threading.Thread(target=recorder.prewarm)
    prewarm.start()
    assert helper.entered.wait(.2)
    shutdown = threading.Thread(target=recorder.shutdown)
    shutdown.start()
    shutdown.join(.05)
    helper.release.set()
    prewarm.join(1)
    shutdown.join(1)
    assert not prewarm.is_alive() and not shutdown.is_alive()
    assert helper.terminated
    assert not helper.live
    assert not recorder.prewarm()


def test_permission_denial_stops_recovery_without_launch(monkeypatch):
    import speakeasy.recorder as module
    monkeypatch.setattr(module, "_permission_blocked", lambda: True, raising=False)
    helper = FakeHelper()
    recorder = _recorder_with(helper)
    assert not recorder.recover("launch_failure")
    assert recorder.state == "permission_blocked"
    assert not helper.launched
