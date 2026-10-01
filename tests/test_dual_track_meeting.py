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
from speakeasy.transcriber import Transcriber, read_wav_mono_f32


class FakeMic:
    def __init__(self, path, *, start_error=None, first_buffer_ns=1_000_000_000):
        self.path = path
        self.start_error = start_error
        self.first_buffer_ns = first_buffer_ns
        self.level = 0.25
        self.elapsed_seconds = 2.0
        self.dropped_frames = 0
        self.writer_failed = False
        self.writer_lagged = False
        self.input_overflows = 0
        self.gap_fills = 0
        self.gap_fill_ms = 0
        self.timeline = {"error_ms": 0}
        self.arrival_timeline = {"error_ms": 0}
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
    def __init__(
        self,
        *,
        failure=None,
        first_buffer_ns=1_350_000_000,
        nonzero_signal=True,
        writer_failed=False,
        writer_lagged=False,
        output_route_start="built_in_speakers",
        output_route_stop="built_in_speakers",
    ):
        self.output_route_start = output_route_start
        self.output_route_stop = output_route_stop
        self.status = "available"
        self.failure = failure
        self.first_buffer_ns = first_buffer_ns
        self.path = None
        self.started = False
        self.nonzero_signal = nonzero_signal
        self.dropped_frames = 0
        self.writer_failed = writer_failed
        self.writer_lagged = writer_lagged
        self.helper_exited = False
        self.helper_exit_reason = None
        self.timeline = None
        self.sample_timeline = None
        self.start_process_ids = []

    def start(self, path, *, process_id=None):
        self.start_process_ids.append(process_id)
        self.path = path
        if self.failure:
            raise SystemAudioUnavailable(self.failure)
        self.started = True
        path.write_bytes(b"system")

    def stop(self):
        return SystemTrackResult(
            self.path,
            self.first_buffer_ns,
            "captured",
            dropped_frames=7,
            nonzero_signal=self.nonzero_signal,
            writer_failed=self.writer_failed,
            writer_lagged=self.writer_lagged,
            output_route_start=self.output_route_start,
            output_route_stop=self.output_route_stop,
            timeline=self.timeline,
            sample_timeline=self.sample_timeline,
        )

    def force_close(self):
        pass

    def take_result(self):
        return SystemTrackResult(
            self.path,
            self.first_buffer_ns,
            self.status,
            nonzero_signal=self.nonzero_signal,
            output_route_start=self.output_route_start,
            output_route_stop=self.output_route_stop,
        )

    def discard(self):
        if self.path:
            self.path.unlink(missing_ok=True)


@pytest.fixture(
    params=[
        (4_750_000_000, 5_100_000_000, {"mic": 0.0, "system": 0.35}),
        (5_250_000_000, 5_000_000_000, {"mic": 0.25, "system": 0.0}),
    ]
)
def synchronized_first_buffers(request):
    mic_ns, system_ns, offsets = request.param
    return mic_ns, system_ns, offsets


def test_dual_track_start_stop_and_first_buffer_offsets(
    spool_dir, synchronized_first_buffers
):
    mic_ns, system_ns, expected_offsets = synchronized_first_buffers
    mic = FakeMic(spool_dir / "mic.wav", first_buffer_ns=mic_ns)
    system = FakeSystem(first_buffer_ns=system_ns)
    recorder = MeetingCaptureRecorder(mic=mic, system=system)

    recorder.start()
    recording = recorder.stop()

    assert mic.started and system.started
    assert recording.capture_mode == "mic_and_system"
    assert recording.track_offsets_seconds == expected_offsets
    assert set(recording.paths) == {mic.path, system.path}
    assert recording.health.system_nonzero_signal is True
    assert recording.health.system_dropped_frames == 7
    assert recording.health.capture_outcome == "captured"
    assert recording.health.fallback_reason is None


def test_missing_system_first_buffer_is_visible_mic_only_fallback(spool_dir):
    mic = FakeMic(spool_dir / "mic.wav")
    recorder = MeetingCaptureRecorder(
        mic=mic, system=FakeSystem(first_buffer_ns=None)
    )

    recorder.start()
    recording = recorder.stop()

    assert recording.capture_mode == "mic_only"
    assert recording.system_audio_status == "no_system_audio"


def test_silent_system_track_is_visible_mic_only_fallback(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(nonzero_signal=False),
    )

    recorder.start()
    recording = recorder.stop()

    assert recording.capture_mode == "mic_only"
    assert recording.system_audio_status == "silent_system_audio"
    assert recording.health.capture_outcome == "fallback"
    assert recording.health.fallback_reason == "silent_system_audio"


def test_writer_health_and_dropped_frames_are_reported_without_content(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(writer_failed=True, writer_lagged=True),
    )

    recorder.start()
    recording = recorder.stop()

    assert recording.capture_mode == "mic_only"
    assert recording.system_audio_status == "system_writer_failed"
    assert recording.health.system_dropped_frames == 7
    assert recording.health.system_writer_failed is True
    assert recording.health.system_writer_lagged is True
    assert set(recording.health.to_dict()) == {
        "mic_first_buffer",
        "system_first_buffer",
        "system_nonzero_signal",
        "mic_dropped_frames",
        "system_dropped_frames",
        "mic_writer_failed",
        "system_writer_failed",
        "mic_writer_lagged",
        "system_writer_lagged",
        "helper_exited",
        "helper_exit_reason",
        "capture_outcome",
        "fallback_reason",
        "capture_mode",
        "capture_scope",
        "output_route_start",
        "output_route_stop",
        "mic_input_overflows",
        "mic_gap_fills",
        "mic_gap_fill_ms",
        "mic_timeline",
        "mic_arrival_timeline",
        "system_timeline",
        "system_sample_timeline",
    }


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


def test_selected_app_unavailable_never_retries_global_capture(spool_dir):
    system = FakeSystem(failure="selected_app_unavailable")
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"), system=system
    )

    recorder.start(system_audio_pid=4242)
    recording = recorder.stop()

    assert system.start_process_ids == [4242]
    assert recording.capture_mode == "mic_only"
    assert recording.capture_scope == "selected_app_unavailable"
    assert recording.system_audio_status == "selected_app_unavailable"
    assert recording.health.capture_scope == "selected_app_unavailable"


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


def test_known_track_splits_on_pause_longer_than_gap():
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 1.0, "one"), Sentence(2.5, 3.0, "two"),   # 1.5 s gap: merge
         Sentence(4.6, 5.0, "three")]                            # 1.6 s gap: split
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 3.0, "one two"), (4.6, 5.0, "three")]


def test_known_track_caps_segment_length():
    sentences = [Sentence(i * 10.0, i * 10.0 + 9.5, f"s{i}") for i in range(13)]
    segments = meetings.known_speaker_segments(sentences)
    assert all(s.end - s.start <= 60.0 for s in segments)
    assert " ".join(s.text for s in segments) == " ".join(f"s{i}" for i in range(13))
    assert len(segments) == 3


def test_known_track_keyword_overrides_win_over_defaults():
    # 0.2 s gap merges under the 1.5 s default, but must split once the
    # caller passes a tighter override.
    gap_segments = meetings.known_speaker_segments(
        [Sentence(0.0, 1.0, "one"), Sentence(1.2, 2.0, "two")],
        max_gap_seconds=0.1,
    )
    assert [(s.start, s.end, s.text) for s in gap_segments] == [
        (0.0, 1.0, "one"), (1.2, 2.0, "two")]

    # 0 s gap merges under the 60 s default cap, but a 6 s span must split
    # once the caller passes a 5 s override.
    cap_segments = meetings.known_speaker_segments(
        [Sentence(0.0, 3.0, "one"), Sentence(3.0, 6.0, "two")],
        max_segment_seconds=5,
    )
    assert [(s.start, s.end, s.text) for s in cap_segments] == [
        (0.0, 3.0, "one"), (3.0, 6.0, "two")]


def test_known_track_reads_gap_constant_from_config_at_call_time(monkeypatch):
    monkeypatch.setattr(config, "KNOWN_SPEAKER_MAX_GAP_SECONDS", 0.1)
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 1.0, "one"), Sentence(1.2, 2.0, "two")]
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 1.0, "one"), (1.2, 2.0, "two")]


def test_known_track_reads_cap_constant_from_config_at_call_time(monkeypatch):
    monkeypatch.setattr(config, "KNOWN_SPEAKER_MAX_SEGMENT_SECONDS", 5.0)
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 3.0, "one"), Sentence(3.0, 6.0, "two")]
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 3.0, "one"), (3.0, 6.0, "two")]


def test_known_track_keeps_segment_exactly_at_cap_boundary():
    # A merged segment landing exactly on the 60 s cap must stay one
    # segment: the boundary check is <=, not <.
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 30.0, "one"), Sentence(30.0, 60.0, "two")]
    )
    assert [(s.start, s.end, s.text) for s in segments] == [(0.0, 60.0, "one two")]


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


def test_transcribe_long_wav_reads_only_requested_chunks(tmp_path):
    path = tmp_path / "meeting.wav"
    samples = np.arange(config.SAMPLE_RATE * 2, dtype=np.int16)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(config.SAMPLE_RATE)
        output.writeframes(samples.tobytes())

    transcriber = Transcriber.__new__(Transcriber)
    observed = []

    def inspect_chunks(total, read_chunk, *, progress, cancel):
        observed.append((total, read_chunk(100, 200), read_chunk(400, 550)))
        return "streamed"

    transcriber._transcribe_long_source = inspect_chunks

    assert transcriber.transcribe_long_wav(path) == "streamed"
    total, first, second = observed[0]
    assert total == len(samples)
    assert len(first) == 100
    assert len(second) == 150
    assert first[0] == pytest.approx(samples[100] / 32768.0)


def test_recording_offsets_use_earliest_valid_track_as_zero(tmp_path):
    recording = MeetingRecording(
        mic_path=tmp_path / "mic.wav",
        system_path=tmp_path / "system.wav",
        mic_start_ns=2_000_000_000,
        system_start_ns=1_500_000_000,
    )
    assert recording.track_offsets_seconds == {"mic": 0.5, "system": 0.0}


def test_output_route_reaches_capture_health(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(output_route_start="built_in_speakers",
                          output_route_stop="bluetooth"),
    )
    recorder.start()
    recording = recorder.stop()
    assert recording.health.output_route_start == "built_in_speakers"
    assert recording.health.output_route_stop == "bluetooth"
    assert recording.health.to_dict()["output_route_stop"] == "bluetooth"


def test_live_health_reports_output_route_while_recording(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(output_route_start="built_in_speakers",
                          output_route_stop="bluetooth"),
    )
    recorder.start()
    health = recorder.health
    assert health.output_route_start == "built_in_speakers"
    assert health.output_route_stop == "bluetooth"
    recorder.stop()


def test_forced_close_health_reports_output_route(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(output_route_start="built_in_speakers",
                          output_route_stop="bluetooth"),
    )
    recorder.start()
    recorder.force_close()
    recording = recorder.take_recording()
    assert recording.health.capture_outcome == "forced_close"
    assert recording.health.output_route_start == "built_in_speakers"
    assert recording.health.output_route_stop == "bluetooth"


def test_track_timelines_and_mic_overflows_reach_capture_health(spool_dir):
    mic = FakeMic(spool_dir / "mic.wav")
    mic.input_overflows = 3
    mic.gap_fills = 2
    mic.gap_fill_ms = 516
    mic.timeline = {"error_ms": 250}
    mic.arrival_timeline = {"error_ms": 251}
    system = FakeSystem()
    system.timeline = {"error_ms": 0}
    system.sample_timeline = {"error_ms": 1}
    recorder = MeetingCaptureRecorder(mic=mic, system=system)

    recorder.start()
    health = recorder.stop().health.to_dict()

    assert health["mic_input_overflows"] == 3
    assert health["mic_gap_fills"] == 2
    assert health["mic_gap_fill_ms"] == 516
    assert health["mic_timeline"] == {"error_ms": 250}
    assert health["mic_arrival_timeline"] == {"error_ms": 251}
    assert health["system_timeline"] == {"error_ms": 0}
    assert health["system_sample_timeline"] == {"error_ms": 1}
