"""Engine meeting flow: begin/end/cancel with every heavy piece faked.

No mic, no model, no sherpa — fakes drive the state machine and prove the
invariants: dictation hotkey off while a meeting runs and re-armed after,
the transcript saved on success and never on cancel, and the spool WAV
deleted on every exit path.
"""

import threading
import time
import wave

import numpy as np
import pytest

from speakeasy import config, meetings
from speakeasy.coreaudio import RecorderBusy
from speakeasy.engine import DictationEngine, MeetingOptions, State
from speakeasy.meeting_recorder import MeetingCaptureHealth, MeetingRecording
from speakeasy.transcriber import MeetingCancelled


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
    engine.diarizer = FakeDiarizer()
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
    stored = meetings.Meeting.load(saved[0])
    assert stored.segments[0].speaker == "Speaker 1"
    assert stored.segments[0].text == "Claude says hello"  # profile applied
    assert not list(spool_dir.iterdir())  # spool deleted after processing
    assert engine._listener.running is True  # hotkey re-armed
    engine.shutdown()


def test_dual_track_meeting_labels_you_and_diarizes_only_remote_track(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    diarizer = FakeRemoteDiarizer()
    engine.diarizer = diarizer
    engine._diarizer_speaker_count = 2
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(expected_speaker_count=2))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert engine.meeting_recorder.capture_mode == "mic_and_system"
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = meetings.Meeting.load(saved[0])
    assert [(s.speaker, s.start) for s in stored.segments] == [
        ("You", 0.1),
        ("Speaker 1", 0.5),
        ("Speaker 2", 1.5),
    ]
    assert diarizer.audio_lengths == [config.SAMPLE_RATE * 2]
    assert stored.capture_mode == "mic_and_system"
    assert stored.expected_remote_speaker_count == 2
    assert stored.track_offsets_seconds == {"mic": 0.0, "system": 0.5}
    assert stored.capture_health["system_nonzero_signal"] is True
    assert stored.capture_health["capture_mode"] == "mic_and_system"
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_dual_track_streams_both_transcripts_before_loading_system_for_diarization(
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
    engine.diarizer = FakeRemoteDiarizer()
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

    assert calls == [
        ("transcribe", recorder.mic_path),
        ("transcribe", recorder.system_path),
        ("full_load", recorder.system_path),
    ]
    stored = meetings.Meeting.load(saved[0])
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
    engine.diarizer = FakeRemoteDiarizer()
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(system_audio_pid=4242))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert recorder.system_audio_pid == 4242
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = meetings.Meeting.load(saved[0])
    raw = stored.path.read_text()
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

    stored = meetings.Meeting.load(saved[0])
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
    engine.diarizer = FakeRemoteDiarizer()
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(
        MeetingOptions(expected_voice_profile_names=("Alice",))
    )
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = meetings.Meeting.load(saved[0])
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

    engine.diarizer = CancellingDiarizer()
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert meetings.list_meetings() == []
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
    assert meetings.list_meetings() == []
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
    assert meetings.Meeting.load(saved[0]).segments[0].speaker == "Alice"
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
    assert meetings.list_meetings() == []  # nothing saved
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


def test_one_remote_speaker_means_two_speakers_in_mic_only_fallback(
    meetings_dir, spool_dir
):
    engine = _engine(spool_dir)
    engine._diarizer_speaker_count = 2
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(expected_speaker_count=1))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = meetings.Meeting.load(saved[0])
    assert engine._diarizer_speaker_count == 2
    assert stored.expected_remote_speaker_count == 1
    engine.shutdown()


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
