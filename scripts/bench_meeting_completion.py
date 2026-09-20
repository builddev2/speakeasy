"""Accelerated synthetic dual-track processing, never live capture.

Uses the production pipeline and private temporary spools. Microphone ASR is
completed before the simulated stop; system ASR and diarization remain after it.
Synthetic repeating voices are workload probes, not speaker-quality acceptance.
"""
import argparse
import contextlib
from concurrent.futures import Future
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--minutes', type=int, choices=(15, 30, 60), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    from speakeasy import config, settings, meetings
    from speakeasy.engine import DictationEngine, MeetingOptions
    from speakeasy.meeting_recorder import MeetingRecording
    from speakeasy.meeting_stream import MeetingASRResult, MeetingASRStatus
    from speakeasy.meeting_benchmark import MeetingTiming
    from speakeasy.transcriber import Transcriber
    import numpy as np
    with wave.open(str(args.fixture)) as source:
        assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, 16000)
        speech = source.readframes(source.getnframes())
    if not speech:
        parser.error('nonempty fixture required')
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as output, tempfile.TemporaryDirectory(prefix='speakeasy-meeting-bench-') as folder, contextlib.redirect_stdout(io.StringIO()):
        root = Path(folder)
        # Different 20-second placement creates silence, alternating turns and overlap.
        for track, offset in (('mic', 0), ('system', 2)):
            block = np.zeros(20 * config.SAMPLE_RATE, dtype=np.int16)
            samples = np.frombuffer(speech, dtype=np.int16)
            start = offset * config.SAMPLE_RATE
            block[start:start + len(samples)] = samples
            with wave.open(str(root / f'{track}.wav'), 'wb') as dest:
                dest.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                for _ in range(args.minutes * 3):
                    dest.writeframes(block.tobytes())
        engine = object.__new__(DictationEngine)
        engine.transcriber = Transcriber()
        engine.diarizer = None
        engine._diarizer_speaker_count = None
        engine._meeting_options = MeetingOptions()
        engine._meeting_cancel = threading.Event()
        engine.profile = None
        engine._user_paused = True
        engine.meeting_processing_error = None
        engine.on_meeting_progress = lambda _: None
        engine.on_meeting_saved = lambda _: None
        engine._set_state = lambda _: None
        engine._idle_state = lambda: None
        settings.meetings_dir = lambda: root
        start = time.perf_counter()
        mic = engine.transcriber.transcribe_long_wav(root / 'mic.wav')
        precompute_ms = (time.perf_counter() - start) * 1000
        future = Future()
        future.set_result(MeetingASRResult(MeetingASRStatus.COMPLETE, transcript=mic))
        timing = MeetingTiming()
        timing.start('stop')
        timing.finish('stop')
        recording = MeetingRecording(mic_path=root / 'mic.wav', system_path=root / 'system.wav', capture_mode='mic_and_system')
        records = []
        timing.emit = lambda status: records.append(timing.record(status))
        engine._process_meeting(recording, timing, future)
        row = dict(workload='accelerated_synthetic_repeating_single_voice_dual_track',
                   minutes=args.minutes, sample_count=1, mic_precompute_ms=precompute_ms,
                   dirty=bool(subprocess.check_output(['git', 'status', '--porcelain']).strip()),
                   capture_stop_measured=False, capture_drops_measured=False,
                   process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   spool_deleted=all(not path.exists() for path in recording.paths),
                   timing=records[0])
        output.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    main()
