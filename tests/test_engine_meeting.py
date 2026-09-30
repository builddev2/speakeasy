"""Engine meeting flow: begin/end/cancel with every heavy piece faked.

No mic, no model, no sherpa — fakes drive the state machine and prove the
invariants: dictation hotkey off while a meeting runs and re-armed after,
the transcript saved on success and never on cancel, and the spool WAV
deleted on every exit path.
"""

import dataclasses
import json
import queue
import threading
import time
import wave
from datetime import datetime, timedelta, timezone as _tz
import sqlite3 as _sqlite3

import numpy as np
import pytest

from speakeasy import config, meeting_benchmark, meeting_import, meetings
from speakeasy.coreaudio import RecorderBusy
from speakeasy.diarization_process import (
    ChildDiarizationRunner, DiarizationFailed, InProcessDiarizationRunner,
)
from speakeasy.engine import DictationEngine, MeetingOptions, State
from speakeasy.meeting_library import EventPerson, MeetingLibrary, NewMeeting, SyncedEvent
from speakeasy.meeting_recorder import MeetingCaptureHealth, MeetingRecording
from speakeasy.meeting_stream import MeetingASRResult, MeetingASRStatus
from speakeasy.transcriber import MeetingCancelled


@pytest.fixture(autouse=True)
def isolate_meeting_latency_log(monkeypatch, tmp_path):
    from speakeasy import dictation_benchmark
    append = dictation_benchmark._append_record
    monkeypatch.setattr(dictation_benchmark, "_append_record",
                        lambda record, path: append(record, tmp_path / "meeting-start.jsonl"))
    path = tmp_path / "meeting-latency.jsonl"
    monkeypatch.setattr(meeting_benchmark, "log_path", lambda: path)
    return path


class SpyListener:
    def __init__(self):
        self.running = True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def pause(self):
        pass

    def resume(self):
        self.running = True


class FakeMeetingRecorder:
    """Writes a real (tiny) spool WAV so the pipeline reads actual audio."""

    def __init__(self, spool_dir):
        self._spool_dir = spool_dir
        self._path = None
        self.elapsed_seconds = 0.0
        self.level = 0.0

    def start(self, *, system_audio_pid=None):
        assert system_audio_pid is None
        self._path = self._spool_dir / "meeting-test.wav"
        with wave.open(str(self._path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(np.ones(config.SAMPLE_RATE, dtype=np.int16).tobytes())

    def stop(self):
        path, self._path = self._path, None
        return path

    def force_close(self):
        pass

    def take_path(self):
        path, self._path = self._path, None
        return path

    def discard(self):
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None


class PretranscribingMeetingRecorder(FakeMeetingRecorder):
    def __init__(self, spool_dir):
        super().__init__(spool_dir)
        self.session = None

    def configure_pretranscription(self, session):
        self.session = session

    def stop(self):
        with wave.open(str(self._path)) as source:
            self.session.add_pcm(source.readframes(source.getnframes()))
        self.session.finish()
        return super().stop()


class Sentence:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
        step = (end - start) / max(len(text.split()), 1)
        self.tokens = []
        for i, w in enumerate(text.split()):
            token = type("Token", (), {})()
            token.start = start + i * step
            token.end = start + (i + 1) * step
            token.duration = step
            token.text = w
            self.tokens.append(token)


class FakeTranscriber:
    def __init__(self, cancel_trap=None):
        self.cancel_trap = cancel_trap  # Event: block until cancel is set

    def transcribe_long(self, audio, *, progress, cancel=None):
        if self.cancel_trap is not None:
            self.cancel_trap.wait(timeout=5.0)
            if cancel is not None and cancel.is_set():
                raise MeetingCancelled
        progress(1.0)
        result = type("Result", (), {})()
        result.sentences = [Sentence(0.0, 1.0, "clod says hello")]
        return result


class PretranscribingFakeTranscriber(FakeTranscriber):
    def __init__(self):
        super().__init__()
        self.live_calls = 0
        self.batch_calls = 0

    def transcribe_meeting_stream(self, session):
        self.live_calls += 1
        while True:
            try:
                session.get(timeout=0.05)
            except queue.Empty:
                if session.finished and session.empty:
                    break
        result = type("Result", (), {})()
        result.sentences = [Sentence(0.0, 1.0, "during capture")]
        return MeetingASRResult(MeetingASRStatus.COMPLETE, result)

    def transcribe_long(self, audio, *, progress, cancel=None):
        self.batch_calls += 1
        return super().transcribe_long(audio, progress=progress, cancel=cancel)


class FakeDiarizer:
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 1.0, 0)]


class FakeDualMeetingRecorder:
    def __init__(self, spool_dir, *, empty_system=False):
        self.mic_path = spool_dir / "meeting-mic.wav"
        self.system_path = spool_dir / "meeting-system.wav"
        self.empty_system = empty_system
        self.elapsed_seconds = 0.0
        self.level = 0.0
        self.capture_mode = "mic_only"
        self.capture_scope = "mic_only"
        self.system_audio_status = "available"
        self.system_audio_pid = None

    @staticmethod
    def _write(path, value, frames=config.SAMPLE_RATE * 2):
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(config.SAMPLE_RATE)
            output.writeframes(np.full(frames, value, dtype=np.int16).tobytes())

    def start(self, *, system_audio_pid=None):
        self.system_audio_pid = system_audio_pid
        self._write(self.mic_path, 1000)
        self._write(self.system_path, 0, frames=0 if self.empty_system else config.SAMPLE_RATE * 2)
        self.capture_mode = "mic_and_system"
        self.capture_scope = "selected" if system_audio_pid is not None else "global"
        self.system_audio_status = "capturing"

    def stop(self):
        return MeetingRecording(
            mic_path=self.mic_path,
            system_path=self.system_path,
            mic_start_ns=1_000_000_000,
            system_start_ns=1_500_000_000,
            capture_mode="mic_and_system",
            system_audio_status="captured",
            health=MeetingCaptureHealth(
                mic_first_buffer=True,
                system_first_buffer=True,
                system_nonzero_signal=not self.empty_system,
                capture_outcome="captured",
                capture_mode="mic_and_system",
                capture_scope=self.capture_scope,
            ),
            capture_scope=self.capture_scope,
        )

    def force_close(self):
        pass

    def take_recording(self):
        return self.stop()

    def discard(self):
        self.mic_path.unlink(missing_ok=True)
        self.system_path.unlink(missing_ok=True)


class FakeDualTranscriber:
    def transcribe_long(self, audio, *, progress, cancel=None):
        progress(1.0)
        result = type("Result", (), {})()
        if float(np.mean(audio)) > 0.001:
            result.sentences = [Sentence(0.1, 0.6, "local hello")]
        else:
            result.sentences = [
                Sentence(0.0, 0.8, "remote one"),
                Sentence(1.0, 1.8, "remote two"),
            ]
        return result


class FakeRemoteDiarizer:
    def __init__(self):
        self.audio_lengths = []

    def diarize(self, samples, progress=lambda f: None):
        self.audio_lengths.append(len(samples))
        progress(1.0)
        return [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def _engine(spool_dir):
    engine = DictationEngine()
    engine._listener = SpyListener()
    engine.meeting_recorder = FakeMeetingRecorder(spool_dir)
    engine.transcriber = FakeTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeDiarizer())
    engine.state = State.READY
    return engine


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_meeting_happy_path(meetings_dir, spool_dir, make_profile):
    engine = _engine(spool_dir)
    engine.profile = make_profile(corrections={"clod": "Claude"})
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert engine._listener.running is False  # dictation dead during meeting
    assert (spool_dir / "meeting-test.wav").exists()

    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert saved, "on_meeting_saved never fired"
    stored = MeetingLibrary().get_meeting(saved[0])
    assert stored.segments[0].speaker == "Speaker 1"
    assert stored.segments[0].text == "Claude says hello"  # profile applied
    assert not list(spool_dir.iterdir())  # spool deleted after processing
    assert engine._listener.running is True  # hotkey re-armed
    engine.shutdown()


def test_meeting_reuses_during_capture_mic_transcript(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = PretranscribingMeetingRecorder(spool_dir)
    transcriber = PretranscribingFakeTranscriber()
    engine.transcriber = transcriber
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert stored.segments[0].text == "during capture"
    assert transcriber.live_calls == 1
    assert transcriber.batch_calls == 0
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_meeting_pretranscription_failure_falls_back_to_spool(
    meetings_dir, spool_dir
):
    class OverflowingTranscriber(PretranscribingFakeTranscriber):
        def transcribe_meeting_stream(self, session):
            self.live_calls += 1
            return MeetingASRResult(MeetingASRStatus.OVERFLOW)

    engine = _engine(spool_dir)
    engine.meeting_recorder = PretranscribingMeetingRecorder(spool_dir)
    transcriber = OverflowingTranscriber()
    engine.transcriber = transcriber
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    assert MeetingLibrary().get_meeting(saved[0]).segments[0].text == "clod says hello"
    assert transcriber.live_calls == 1
    assert transcriber.batch_calls == 1
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_meeting_happy_path_records_content_free_phase_timing(
    meetings_dir, spool_dir, isolate_meeting_latency_log
):
    engine = _engine(spool_dir)

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    records = meeting_benchmark.read_records(isolate_meeting_latency_log)
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "success"
    assert record["capture_mode"] == "mic_only"
    assert record["mic_frames"] == config.SAMPLE_RATE
    assert record["mic_asr_ms"] is not None
    assert record["diarization_ms"] is not None
    assert record["alignment_ms"] is not None
    assert record["save_ms"] is not None
    assert set(record) == set(meeting_benchmark.FIELDS)
    engine.shutdown()


def test_dual_track_meeting_labels_you_and_diarizes_only_remote_track(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    diarizer = FakeRemoteDiarizer()
    engine.diarization_runner = InProcessDiarizationRunner(diarizer)
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(expected_speaker_count=2))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert engine.meeting_recorder.capture_mode == "mic_and_system"
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert [(s.speaker, s.start) for s in stored.segments] == [
        ("You", 0.1),
        ("Speaker 1", 0.5),
        ("Speaker 2", 1.5),
    ]
    assert diarizer.audio_lengths == [config.SAMPLE_RATE * 2]
    assert stored.capture_mode == "mic_and_system"
    assert stored.track_offsets_seconds == {"mic": 0.0, "system": 0.5}
    assert stored.capture_health["system_nonzero_signal"] is True
    assert stored.capture_health["capture_mode"] == "mic_and_system"
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_dual_track_streams_both_transcripts_and_never_loads_system_in_engine(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    recorder = FakeDualMeetingRecorder(spool_dir)
    engine.meeting_recorder = recorder
    calls = []

    class StreamingTranscriber:
        def transcribe_long_wav(self, path, *, progress, cancel=None):
            calls.append(("transcribe", path))
            progress(1.0)
            result = type("Result", (), {})()
            result.sentences = (
                [Sentence(0.1, 0.6, "local hello")]
                if path == recorder.mic_path
                else [Sentence(0.0, 0.8, "remote one")]
            )
            return result

        def transcribe_long(self, audio, *, progress, cancel=None):
            raise AssertionError("16 kHz meeting spools must use chunked WAV reads")

    engine.transcriber = StreamingTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    read_meeting_track = engine._read_meeting_track

    def track_full_load(path):
        calls.append(("full_load", path))
        return read_meeting_track(path)

    engine._read_meeting_track = track_full_load
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    # The diarization track is read by the runner (a child in the app), never
    # loaded into the engine's process.
    assert calls == [
        ("transcribe", recorder.mic_path),
        ("transcribe", recorder.system_path),
    ]
    stored = MeetingLibrary().get_meeting(saved[0])
    assert [(segment.speaker, segment.start) for segment in stored.segments] == [
        ("You", 0.1),
        ("Speaker 1", 0.5),
    ]
    engine.shutdown()


def test_selected_application_scope_persists_without_pid_or_name(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    recorder = FakeDualMeetingRecorder(spool_dir)
    engine.meeting_recorder = recorder
    engine.transcriber = FakeDualTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(system_audio_pid=4242))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert recorder.system_audio_pid == 4242
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    raw = json.dumps(dataclasses.asdict(stored), default=str)
    assert stored.capture_scope == "selected"
    assert stored.capture_health["capture_scope"] == "selected"
    assert "4242" not in raw
    assert "application_name" not in raw
    engine.shutdown()


def test_empty_system_track_falls_back_to_existing_mic_diarization(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir, empty_system=True)
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert stored.capture_mode == "mic_only"
    assert stored.system_audio_status == "empty_track"
    assert stored.segments[0].speaker == "Speaker 1"
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_enrolled_profile_matching_applies_only_to_remote_track(
    meetings_dir, spool_dir, monkeypatch
):
    import speakeasy.voice_profiles as voice_profiles

    identified_lengths = []

    class FakeVoiceProfiles:
        def identify(self, audio, turns, expected_names, *, cancelled):
            identified_lengths.append(len(audio))
            return [
                meetings.DiarizationTurn(0.0, 0.8, 4, 0.9, False, "Alice"),
                meetings.DiarizationTurn(1.0, 1.8, 9),
            ]

    monkeypatch.setattr(voice_profiles, "VoiceProfileStore", FakeVoiceProfiles)
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(
        MeetingOptions(expected_voice_profile_names=("Alice",))
    )
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert [s.speaker for s in stored.segments] == ["You", "Alice", "Speaker 2"]
    assert identified_lengths == [config.SAMPLE_RATE * 2]
    engine.shutdown()


def test_cancel_set_during_remote_diarization_discards_both_tracks(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()

    class CancellingDiarizer(FakeRemoteDiarizer):
        def diarize(self, samples, progress=lambda f: None):
            engine.cancel_meeting_processing()
            return super().diarize(samples, progress)

    engine.diarization_runner = InProcessDiarizationRunner(CancellingDiarizer())
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert MeetingLibrary().count_meetings() == 0
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_dual_track_processing_failure_cleans_both_spools(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)

    class FailingTranscriber:
        def transcribe_long(self, audio, *, progress, cancel=None):
            raise RuntimeError("synthetic failure")

    engine.transcriber = FailingTranscriber()
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert MeetingLibrary().count_meetings() == 0
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_selected_voice_profile_is_used_locally(
    meetings_dir, spool_dir, monkeypatch
):
    import speakeasy.voice_profiles as voice_profiles

    calls = []

    class FakeVoiceProfiles:
        def identify(self, audio, turns, expected_names, *, cancelled):
            calls.append((expected_names, callable(cancelled)))
            return [meetings.DiarizationTurn(0.0, 1.0, 0, 0.9, False, "Alice")]

    monkeypatch.setattr(voice_profiles, "VoiceProfileStore", FakeVoiceProfiles)
    engine = _engine(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    engine.begin_meeting(
        MeetingOptions(expected_voice_profile_names=("Alice",))
    )
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert calls == [(["Alice"], True)]
    assert MeetingLibrary().get_meeting(saved[0]).segments[0].speaker == "Alice"
    engine.shutdown()


def test_begin_refused_unless_ready(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.state = State.TRANSCRIBING
    engine.begin_meeting()
    time.sleep(0.2)
    assert engine.state is State.TRANSCRIBING
    assert engine._listener.running is True
    engine.shutdown()


def test_selected_application_pid_must_still_be_live():
    with pytest.raises(ValueError, match="no longer available"):
        MeetingOptions(system_audio_pid=0)


def test_begin_refused_while_a_coreaudio_teardown_is_in_flight(
    meetings_dir, spool_dir
):
    # Begin Meeting while a prior stop is still unwinding in the HAL: the open
    # must be refused, not attempted. _begin_meeting runs on the control thread
    # with no watchdog, so a deadlock here would freeze the whole pipeline —
    # instead the engine stays idle with the hotkey live.
    engine = _engine(spool_dir)

    def refuse():
        raise RecorderBusy()

    engine.meeting_recorder.start = refuse

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.READY and not engine._meeting_active)
    assert engine._listener.running is True  # dictation re-armed, not stranded off
    assert not list(spool_dir.iterdir())  # no orphan spool

    # The control thread is still alive: a later meeting still goes through.
    engine.meeting_recorder = FakeMeetingRecorder(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    engine.shutdown()


def test_cancel_discards_everything(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    trap = threading.Event()
    engine.transcriber = FakeTranscriber(cancel_trap=trap)

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_PROCESSING)

    engine.cancel_meeting_processing()
    trap.set()  # release the trapped transcriber; it sees cancel and raises
    assert _wait_for(lambda: engine.state is State.READY)
    assert MeetingLibrary().count_meetings() == 0  # nothing saved
    assert not list(spool_dir.iterdir())  # spool deleted anyway
    assert engine._listener.running is True
    engine.shutdown()


def test_shutdown_mid_meeting_discards_spool(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.shutdown()
    engine.meeting_recorder.discard()
    assert not list(spool_dir.iterdir())


def test_resume_does_not_rearm_hotkey_mid_meeting(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.resume()  # e.g. training window closing
    assert engine._listener.running is False
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    engine.shutdown()


def test_can_train_gates(meetings_dir, spool_dir, make_profile):
    engine = _engine(spool_dir)

    engine.profile = None  # Guest
    assert engine.can_train is False

    engine.profile = make_profile()
    assert engine.can_train is True

    engine.transcriber = None  # model still loading
    assert engine.can_train is False
    engine.transcriber = FakeTranscriber()

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert engine.can_train is False  # meeting owns the hotkey and the mic
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert engine.can_train is True
    engine.shutdown()


def test_meeting_options_validate():
    with pytest.raises(ValueError):
        MeetingOptions(expected_speaker_count=0)
    with pytest.raises(ValueError):
        MeetingOptions(expected_speaker_count=21)


def test_correct_last_dictation_learns_active_profile(spool_dir, make_profile):
    engine = _engine(spool_dir)
    engine.profile = make_profile()
    engine.last_dictation_heard = "clod"
    assert engine.correct_last_dictation("Claude") is True
    assert engine.profile.apply("clod") == "Claude"
    engine.shutdown()


def test_switching_profile_clears_last_dictation(spool_dir, make_profile):
    engine = _engine(spool_dir)
    engine.profile = make_profile("First")
    engine.last_dictation_heard = "clod"
    engine.last_dictation_text = "Claude"
    engine.set_profile(make_profile("Second"))
    assert engine.last_dictation_heard is None
    assert engine.last_dictation_text is None
    engine.shutdown()


def test_meeting_setup_failure_does_not_latch_active_or_discard_error(spool_dir, tmp_path, monkeypatch):
    from speakeasy import dictation_benchmark
    records = []
    monkeypatch.setattr(dictation_benchmark, '_append_record', lambda record, path: records.append(record))
    engine = _engine(spool_dir)
    def fail(session):
        raise RuntimeError('private error text')
    engine.meeting_recorder.configure_pretranscription = fail
    engine.transcriber.transcribe_meeting_stream = lambda session: None
    try:
        engine._begin_meeting(MeetingOptions())
        assert engine.state is State.READY
        assert not engine._meeting_active
        assert engine._listener.running
        assert engine.meeting_start_error == 'setup_failed'
        assert records[-1]['reason'] == 'setup_failed'
        assert 'private error text' not in str(records)
    finally:
        engine.control.shutdown(wait=True)
        engine.worker.shutdown(wait=True)


def test_meeting_start_failure_is_visible_and_next_attempt_can_start(spool_dir, monkeypatch):
    from speakeasy import dictation_benchmark
    monkeypatch.setattr(dictation_benchmark, '_append_record', lambda *args: None)
    engine = _engine(spool_dir)
    def fail(**kwargs):
        raise RecorderBusy()
    engine.meeting_recorder.start = fail
    try:
        engine._begin_meeting(MeetingOptions())
        assert not engine._meeting_active
        assert engine.meeting_start_error == 'microphone_busy'
        engine.meeting_recorder = FakeMeetingRecorder(spool_dir)
        engine._begin_meeting(MeetingOptions())
        assert engine.state is State.MEETING_RECORDING
        assert engine.meeting_start_error is None
        engine.meeting_recorder.stop()
    finally:
        engine.control.shutdown(wait=True)
        engine.worker.shutdown(wait=True)


def test_processing_failure_is_visible_without_exception_content(meetings_dir, spool_dir, capsys):
    engine = _engine(spool_dir)
    class Broken(FakeTranscriber):
        def transcribe_long(self, *args, **kwargs):
            raise OSError('private transcript and path')
    engine.transcriber = Broken()
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    try:
        assert engine.meeting_processing_error == 'processing_failed'
        assert not list(spool_dir.iterdir())
        assert MeetingLibrary().count_meetings() == 0
        err = capsys.readouterr().err
        assert 'Traceback' in err and 'OSError' in err
        assert 'private transcript' not in err
    finally:
        engine.shutdown()


def test_cleanup_failure_does_not_skip_other_track_or_rearm(meetings_dir, spool_dir, monkeypatch):
    from pathlib import Path
    engine = _engine(spool_dir)
    mic, system = spool_dir / 'mic.wav', spool_dir / 'system.wav'
    mic.write_bytes(b'bad')
    system.write_bytes(b'bad')
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == mic:
            raise OSError('private disk detail')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    try:
        engine._process_meeting(MeetingRecording(mic_path=mic, system_path=system))
        assert engine.meeting_processing_error == 'cleanup_failed'
        assert not system.exists()
        assert not engine._meeting_active
        assert engine._listener.running
    finally:
        monkeypatch.setattr(Path, 'unlink', original)
        mic.unlink()
        engine.shutdown()


def test_progress_after_cancel_keeps_cancellation_visible(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    messages = []
    engine.on_meeting_progress = messages.append
    class Cancelling(FakeTranscriber):
        def transcribe_long(self, audio, *, progress, cancel=None):
            engine.cancel_meeting_processing()
            progress(0.5)
            return super().transcribe_long(audio, progress=progress, cancel=cancel)
    engine.transcriber = Cancelling()
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    try:
        assert messages[-1].startswith('Cancelling')
        assert MeetingLibrary().count_meetings() == 0
        assert not list(spool_dir.iterdir())
    finally:
        engine.shutdown()


class ShortFakeMeetingRecorder(FakeMeetingRecorder):
    """Writes far less audio than the wall-clock gap the test sleeps through,
    so a lost start time (falling back to now() - duration) lands clearly
    after `after_begin` instead of within noise distance of it."""

    def start(self, *, system_audio_pid=None):
        assert system_audio_pid is None
        self._path = self._spool_dir / "meeting-test.wav"
        with wave.open(str(self._path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            frames = max(1, int(config.SAMPLE_RATE * 0.1))
            w.writeframes(np.ones(frames, dtype=np.int16).tobytes())


def test_meeting_start_time_is_capture_start(meetings_dir, spool_dir):
    from datetime import datetime

    engine = _engine(spool_dir)
    engine.meeting_recorder = ShortFakeMeetingRecorder(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    before = datetime.now().astimezone().replace(microsecond=0)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    after_begin = datetime.now().astimezone()
    time.sleep(1.1)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    stored = MeetingLibrary().get_meeting(saved[0])
    assert before <= stored.local_start <= after_begin
    assert stored.source == "recorded" and not stored.timestamps_approximate
    engine.shutdown()


def test_process_meeting_uses_recordings_started_at(meetings_dir, spool_dir):
    from datetime import datetime, timedelta, timezone

    engine = _engine(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    mic = spool_dir / "mic.wav"
    with wave.open(str(mic), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(np.ones(config.SAMPLE_RATE, dtype=np.int16).tobytes())
    started_at = datetime(2026, 9, 24, 13, 17, 23, tzinfo=timezone(timedelta(hours=-4)))
    try:
        engine._process_meeting(MeetingRecording(mic_path=mic, started_at=started_at))
        assert saved, "on_meeting_saved never fired"
        stored = MeetingLibrary().get_meeting(saved[0])
        assert stored.started_at == "2026-09-24T17:17:23Z"
    finally:
        engine.shutdown()


def test_upgrade_library_imports_and_publishes_status(meetings_dir, spool_dir):
    import json as _json
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    # The very first status is published synchronously by upgrade_library()
    # itself, before the job is even queued on the worker.
    assert statuses[0] == {"state": "upgrading", "done": 0, "total": 1, "skipped": []}
    assert _wait_for(lambda: statuses and statuses[-1]["state"] == "done")
    assert statuses[-1] == {"state": "done", "done": 1, "total": 1, "skipped": []}
    assert engine.library_status == statuses[-1]
    assert MeetingLibrary().count_meetings() == 1
    engine.shutdown()


def test_upgrade_library_reports_skipped_files_without_leaking_content(
    meetings_dir, spool_dir, capsys
):
    import json as _json
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    bad_name = "20260924-134024-bbbb.json"
    (meetings_dir / bad_name).write_text("not valid json {{{ private secret content")
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    assert _wait_for(lambda: statuses and statuses[-1]["state"] == "done")
    assert statuses[-1]["state"] == "done"
    assert statuses[-1]["skipped"] == [{"file": bad_name, "reason": "JSONDecodeError"}]
    assert MeetingLibrary().count_meetings() == 1
    out = capsys.readouterr().out
    assert f"skipped {bad_name}:" in out
    assert "private secret content" not in out
    engine.shutdown()


def test_upgrade_library_is_a_no_op_without_legacy_files(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    # Wait on a sentinel submitted to the same single-worker executor: since
    # ThreadPoolExecutor(max_workers=1) runs jobs in submission order, this
    # future only resolves after upgrade_library's job (if any) has already
    # run, so the test cannot pass before that job would have published.
    engine.worker.submit(lambda: None).result(timeout=5.0)
    assert statuses == [] and engine.library_status["state"] == "idle"
    engine.shutdown()


def test_upgrade_library_publishes_queued_state_synchronously(meetings_dir, spool_dir):
    # Spec: publish the "upgrading" placeholder the moment the job is
    # queued, not only once the worker (behind model load) gets to it, so
    # the UI shows "Importing meetings…" instead of the empty state during
    # the first-launch delay. This is checked with no wait: the publish
    # must happen synchronously inside upgrade_library() itself.
    import json as _json
    for i in range(3):
        (meetings_dir / f"2026092{i}-134023-aaa{i}.json").write_text(_json.dumps({
            "id": f"2026092{i}-134023-aaa{i}", "title": "t", "created": "2026-09-24T13:40:23",
            "duration_seconds": 60, "segments": [
                {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    # Block the worker before queueing the upgrade job. If the "upgrading"
    # placeholder were published from inside _upgrade_library (i.e. moved
    # onto the worker) instead of synchronously by upgrade_library() on the
    # calling thread, it would not appear until `release` is set below —
    # this is what makes the "synchronous" claim in the test name actually
    # checked, rather than merely plausible from timing.
    release = threading.Event()
    engine.worker.submit(release.wait)
    engine.upgrade_library()
    assert statuses == [{"state": "upgrading", "done": 0, "total": 3, "skipped": []}]
    assert engine.library_status == statuses[0]
    release.set()
    engine.worker.submit(lambda: None).result(timeout=5.0)
    engine.shutdown()


def test_upgrade_library_survives_count_meetings_error_at_queue_time(
    meetings_dir, spool_dir, monkeypatch
):
    # Spec: upgrade_library()'s count_meetings() check runs on the main
    # thread from applicationDidFinishLaunching_ (ui/menubar.py) -- the
    # app's very first DB touch at launch, ahead of engineStateChanged_,
    # main_window.show(), the permissions guidance, and the hotkey. A DB
    # error there must not raise and abort the rest of launch: skip the
    # queued placeholder and still submit the worker job, which re-checks
    # count_meetings() inside its own try and publishes "failed".
    import json as _json
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)

    def boom(self):
        raise RuntimeError("synthetic db failure")

    monkeypatch.setattr(MeetingLibrary, "count_meetings", boom)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()  # must not raise
    assert _wait_for(lambda: statuses and statuses[-1]["state"] == "failed")
    # No queued placeholder was published: the failed status is the only one.
    assert statuses == [statuses[-1]]
    assert statuses[-1]["state"] == "failed"
    engine.shutdown()


def test_upgrade_library_worker_publishes_idle_if_meeting_appears_before_it_runs(
    meetings_dir, spool_dir
):
    # Spec: upgrade_library() decides whether to publish "upgrading" from a
    # main-thread count_meetings() check at queue time; _upgrade_library()
    # re-checks count_meetings() when it actually runs on the worker. If a
    # meeting was saved in between (so the job is now a no-op), the worker
    # must publish "idle" explicitly so the queued banner doesn't stick.
    from datetime import datetime, timezone

    import json as _json
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    # Simulate the race directly: the queued placeholder was already
    # published (as upgrade_library() would when the library was empty)...
    engine._publish_library_status({"state": "upgrading", "done": 0, "total": 1, "skipped": []})
    statuses.clear()
    # ...but by the time the worker job runs, a meeting has appeared.
    MeetingLibrary().save_meeting(NewMeeting(
        segments=[], duration_seconds=1.0,
        started_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
    ))
    engine._upgrade_library()
    assert statuses == [{"state": "idle", "done": 0, "total": 0, "skipped": []}]
    engine.shutdown()


def test_upgrade_library_is_a_no_op_when_library_already_has_meetings(
    meetings_dir, spool_dir
):
    # Spec: auto-import only runs when the library is empty. Otherwise a
    # malformed legacy file left in meetings/ would bring the upgrade banner
    # back on every launch; manual re-import stays available separately.
    import json as _json
    from datetime import datetime, timezone

    library = MeetingLibrary()
    library.save_meeting(NewMeeting(
        segments=[], duration_seconds=1.0,
        started_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
    ))
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    # See test_upgrade_library_is_a_no_op_without_legacy_files: waiting on a
    # sentinel submitted after upgrade_library's own job guarantees that job
    # (which does run here, since a legacy file exists) has already
    # completed before the assertion below.
    engine.worker.submit(lambda: None).result(timeout=5.0)
    assert statuses == []
    engine.shutdown()


def test_upgrade_library_publishes_failed_status_with_details(
    meetings_dir, spool_dir, monkeypatch
):
    (meetings_dir / "20260924-134023-aaaa.json").write_text("not json meeting data")
    engine = _engine(spool_dir)

    def boom(library=None, progress=None):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(meeting_import, "import_json_meetings", boom)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    assert _wait_for(lambda: statuses and statuses[-1]["state"] == "failed")
    assert statuses[-1] == {
        "state": "failed", "done": 0, "total": 0,
        "skipped": [{"file": "", "reason": "RuntimeError"}],
    }
    engine.shutdown()


_REF = EventPerson("Refayet K", "refayet@example.com", "organizer")


def _seed_events(specs):
    """Seed several events in ONE window: a later replace_calendar_window
    call deletes events the earlier one wrote."""
    now = datetime.now(_tz.utc).replace(microsecond=0)
    events = []
    for key, minutes_ago, title, other in specs:
        start = now - timedelta(minutes=minutes_ago)
        events.append(SyncedEvent(key, "Work", title, start, start + timedelta(minutes=30),
                                  False, False, other, (_REF,)))
    MeetingLibrary().replace_calendar_window(
        events, now - timedelta(hours=2), now + timedelta(hours=2))


def _seed_event(key="ev-now", minutes_ago=3, title="Weekly 1:1 \u2014 Refayet", other=1):
    _seed_events([(key, minutes_ago, title, other)])


def _record(engine, options=None):
    saved = []
    engine.on_meeting_saved = saved.append
    engine.begin_meeting(options)
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    return saved


def _finish(engine, saved):
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    return MeetingLibrary().get_meeting(saved[0])


def test_recording_takes_the_matching_event(meetings_dir, spool_dir):
    _seed_event()
    engine = _engine(spool_dir)
    saved = _record(engine)
    assert _wait_for(lambda: engine.meeting_event is not None)
    stored = _finish(engine, saved)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 \u2014 Refayet", "ev-now", ["Refayet K"])
    assert engine.meeting_event is None
    engine.shutdown()


def test_explicit_event_key_wins(meetings_dir, spool_dir):
    _seed_events([("near", 1, "Near", 1), ("chosen", 20, "Chosen", 1)])
    engine = _engine(spool_dir)
    stored = _finish(engine, _record(engine, MeetingOptions(calendar_event_key="chosen")))
    assert stored.title == "Chosen"
    engine.shutdown()


def test_no_event_keeps_default_title(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    stored = _finish(engine, _record(engine))
    assert stored.title.startswith("Meeting \u2014 ") and stored.calendar_event_id is None
    engine.shutdown()


def test_calendar_cap_merges_remote_speakers(meetings_dir, spool_dir):
    _seed_event(other=1)
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    stored = _finish(engine, _record(engine))
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1"]
    engine.shutdown()


def test_manual_count_beats_calendar(meetings_dir, spool_dir):
    _seed_event(other=1)
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    stored = _finish(engine, _record(engine, MeetingOptions(expected_speaker_count=2)))
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1", "Speaker 2"]
    engine.shutdown()


def test_link_and_unlink_while_recording(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    states = []
    engine.on_state_changed = states.append
    saved = _record(engine)
    _seed_event("later")
    engine.link_meeting_event("later")
    assert _wait_for(lambda: engine.meeting_event is not None)
    assert states.count(State.MEETING_RECORDING) >= 2   # Dock refreshed
    engine.link_meeting_event(None)
    assert _wait_for(lambda: engine.meeting_event is None)
    assert _finish(engine, saved).calendar_event_id is None
    engine.shutdown()


def test_busy_library_at_start_records_unlinked(meetings_dir, spool_dir, monkeypatch):
    _seed_event()
    engine = _engine(spool_dir)

    def busy(*a, **k):
        raise _sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(engine.library, "calendar_events_overlapping", busy)
    stored = _finish(engine, _record(engine))
    assert stored.calendar_event_id is None
    engine.shutdown()


def test_event_deleted_before_save_uses_held_copy(meetings_dir, spool_dir):
    _seed_event()
    engine = _engine(spool_dir)
    saved = _record(engine)
    assert _wait_for(lambda: engine.meeting_event is not None)
    MeetingLibrary().clear_calendar_cache()
    stored = _finish(engine, saved)
    assert (stored.title, stored.people) == ("Weekly 1:1 \u2014 Refayet", ["Refayet K"])
    engine.shutdown()


def test_saved_event_is_reread_at_save_time(meetings_dir, spool_dir):
    _seed_event(title="Old title")
    engine = _engine(spool_dir)
    saved = _record(engine)
    assert _wait_for(lambda: engine.meeting_event is not None)
    _seed_event(title="Renamed in Calendar")   # same key, same window: replaces it
    stored = _finish(engine, saved)
    assert stored.title == "Renamed in Calendar"
    engine.shutdown()


class ThreeVoiceDiarizer:
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 0.3, 0), (0.3, 0.6, 1), (0.6, 0.9, 2)]


class ThreeSentenceTranscriber:
    def transcribe_long(self, audio, *, progress, cancel=None):
        progress(1.0)
        result = type("Result", (), {})()
        result.sentences = [Sentence(0.0, 0.3, "one"), Sentence(0.3, 0.6, "two"),
                            Sentence(0.6, 0.9, "three")]
        return result


def _mic_only_voices(engine, spool_dir):
    engine.transcriber = ThreeSentenceTranscriber()
    # Fixed embeddings: voices 0 and 1 alike, voice 2 different.
    vectors = iter([np.array([1.0, 0.0]), np.array([0.9, 0.1]), np.array([0.0, 1.0])])
    engine.diarization_runner = InProcessDiarizationRunner(
        ThreeVoiceDiarizer(), embed=lambda audio: next(vectors))
    stored = _finish(engine, _record(engine))
    return {s.speaker for s in stored.segments}


def test_calendar_cap_applies_to_mic_only(meetings_dir, spool_dir):
    # other=1 -> mic-only cap 2 (you + 1); dual-track cap 1 would merge to 1.
    _seed_event(other=1)
    engine = _engine(spool_dir)
    voices = _mic_only_voices(engine, spool_dir)
    assert len(voices) == 2, voices
    engine.shutdown()


def test_no_calendar_cap_leaves_mic_only_voices(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    assert len(_mic_only_voices(engine, spool_dir)) == 3
    engine.shutdown()


class FailingRunner:
    def __init__(self, reason="child_exited"):
        self.reason = reason
        self.calls = 0

    def __call__(self, wav_path, **kwargs):
        self.calls += 1
        raise DiarizationFailed(self.reason)


def test_default_runner_is_a_child_process(spool_dir):
    engine = DictationEngine()
    assert isinstance(engine.diarization_runner, ChildDiarizationRunner)
    assert not hasattr(engine, "diarizer")
    assert not hasattr(engine, "speaker_embedder")
    engine.shutdown()


def test_diarization_failure_saves_transcript_with_one_label(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    runner = FailingRunner("child_exited")
    engine.diarization_runner = runner
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    assert runner.calls == 1
    stored = MeetingLibrary().get_meeting(saved[0])
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1"]
    assert "remote one" in stored.segments[1].text
    assert stored.capture_health["diarization_status"] == "failed"
    assert stored.capture_health["diarization_failure"] == "child_exited"
    assert engine.meeting_processing_error is None
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_mic_only_diarization_failure_saves_transcript(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.diarization_runner = FailingRunner("child_error:ValueError")
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert [(s.speaker, s.text) for s in stored.segments] == [("Speaker 1", "clod says hello")]
    assert stored.capture_health["diarization_failure"] == "child_error:ValueError"
    engine.shutdown()


def test_successful_diarization_records_ok(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    stored = MeetingLibrary().get_meeting(saved[0])
    assert stored.capture_health["diarization_status"] == "ok"
    assert "diarization_failure" not in stored.capture_health
    engine.shutdown()


def test_cancel_from_runner_saves_nothing(meetings_dir, spool_dir):
    engine = _engine(spool_dir)

    def cancelling_runner(wav_path, **kwargs):
        engine.cancel_meeting_processing()
        raise MeetingCancelled

    engine.diarization_runner = cancelling_runner
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert MeetingLibrary().count_meetings() == 0
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_runner_receives_path_and_meeting_options(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    seen = {}

    def recording_runner(wav_path, **kwargs):
        seen["path_exists"] = wav_path.exists()
        seen.update(kwargs)
        kwargs["on_phase"]("voice_identification")
        return [(0.0, 1.0, 0)]

    engine.diarization_runner = recording_runner
    engine.begin_meeting(MeetingOptions(expected_speaker_count=3,
                                        expected_voice_profile_names=("Alice",)))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert seen["path_exists"] is True
    assert seen["expected_count"] == 3
    assert tuple(seen["voice_profile_names"]) == ("Alice",)
    assert seen["cancel"] is engine._meeting_cancel
    engine.shutdown()


def test_timing_keeps_both_diarization_stages(
    meetings_dir, spool_dir, isolate_meeting_latency_log
):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    record = meeting_benchmark.read_records(isolate_meeting_latency_log)[0]
    assert record["diarization_ms"] is not None
    assert record["voice_identification_ms"] is not None
    engine.shutdown()
