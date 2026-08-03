"""Dual-track meeting capture and pure timeline composition."""

import wave

import numpy as np
import pytest

from speakeasy import config, meetings
from speakeasy.meeting_recorder import (
    MeetingCaptureRecorder,
    MeetingRecording,
)
from speakeasy.system_audio import SystemAudioUnavailable, SystemTrackResult
from speakeasy.transcriber import read_wav_mono_f32


class FakeMic:
    def __init__(self, path, *, start_error=None):
        self.path = path
        self.start_error = start_error
        self.first_buffer_ns = 1_000_000_000
        self.level = 0.25
        self.elapsed_seconds = 2.0
        self.started = False

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True
        self.path.write_bytes(b"mic")

    def stop(self):
        return self.path

    def force_close(self):
        pass

    def take_path(self):
        return self.path

    def discard(self):
        self.path.unlink(missing_ok=True)


class FakeSystem:
    def __init__(self, *, failure=None, first_buffer_ns=1_350_000_000):
        self.status = "available"
        self.failure = failure
        self.first_buffer_ns = first_buffer_ns
        self.path = None
        self.started = False

    def start(self, path):
        self.path = path
        if self.failure:
            raise SystemAudioUnavailable(self.failure)
        self.started = True
        path.write_bytes(b"system")

    def stop(self):
        return SystemTrackResult(
            self.path, self.first_buffer_ns, "captured", dropped_frames=7
        )

    def force_close(self):
        pass

    def take_result(self):
        return SystemTrackResult(self.path, self.first_buffer_ns, self.status)

    def discard(self):
        if self.path:
            self.path.unlink(missing_ok=True)


def test_dual_track_start_stop_and_first_buffer_offsets(spool_dir):
    mic = FakeMic(spool_dir / "mic.wav")
    system = FakeSystem()
    recorder = MeetingCaptureRecorder(mic=mic, system=system)

    recorder.start()
    recording = recorder.stop()

    assert mic.started and system.started
    assert recording.capture_mode == "mic_and_system"
    assert recording.track_offsets_seconds == {"mic": 0.0, "system": 0.35}
    assert set(recording.paths) == {mic.path, system.path}


def test_system_start_denial_falls_back_visibly_to_mic_only(spool_dir):
    mic = FakeMic(spool_dir / "mic.wav")
    recorder = MeetingCaptureRecorder(
        mic=mic, system=FakeSystem(failure="permission_denied_or_unavailable")
    )

    recorder.start()
    recording = recorder.stop()

    assert recording.capture_mode == "mic_only"
    assert recording.system_audio_status == "permission_denied_or_unavailable"
    assert recording.system_path is None


def test_mic_start_failure_does_not_open_system_track(spool_dir):
    system = FakeSystem()
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav", start_error=RuntimeError("mic")),
        system=system,
    )

    with pytest.raises(RuntimeError, match="mic"):
        recorder.start()
    assert system.started is False


def _segment(speaker, start, end, text):
    return meetings.MeetingSegment(speaker, start, end, text)


def test_shift_and_merge_interleaved_tracks_without_text_deduplication():
    local = [_segment("You", 0.0, 0.8, "yes"), _segment("You", 2.0, 2.4, "yes")]
    remote = [
        _segment("Speaker 1", 0.2, 0.9, "hello"),
        _segment("Speaker 2", 1.2, 1.8, "yes"),
    ]

    meetings.shift_segments(remote, 0.5)
    merged = meetings.merge_tracks(local, remote)

    assert [(s.speaker, s.start, s.text) for s in merged] == [
        ("You", 0.0, "yes"),
        ("Speaker 1", 0.7, "hello"),
        ("Speaker 2", 1.7, "yes"),
        ("You", 2.0, "yes"),
    ]


class Sentence:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def test_known_mic_track_is_always_you():
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 0.5, "hello"), Sentence(0.7, 1.0, "again")]
    )
    assert len(segments) == 1
    assert segments[0].speaker == "You"
    assert segments[0].text == "hello again"


def test_read_wav_downmixes_and_resamples_off_callback(tmp_path):
    path = tmp_path / "stereo-8k.wav"
    left = np.linspace(-0.5, 0.5, 800, dtype=np.float32)
    right = -left
    stereo = np.column_stack((left, right))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes((stereo * 32767).astype(np.int16).tobytes())

    audio = read_wav_mono_f32(path)

    assert len(audio) == 1_600
    assert np.max(np.abs(audio)) < 1e-4


def test_recording_offsets_use_earliest_valid_track_as_zero(tmp_path):
    recording = MeetingRecording(
        mic_path=tmp_path / "mic.wav",
        system_path=tmp_path / "system.wav",
        mic_start_ns=2_000_000_000,
        system_start_ns=1_500_000_000,
    )
    assert recording.track_offsets_seconds == {"mic": 0.5, "system": 0.0}
