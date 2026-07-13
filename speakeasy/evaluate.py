"""Offline ASR and diarization evaluator.

Usage:
  python -m speakeasy.evaluate audio.wav --reference reference.json
  python -m speakeasy.evaluate audio.wav --text transcript.txt --rttm speakers.rttm
"""

import argparse
import json
import re
import resource
import time
from pathlib import Path

from . import config, meetings


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w']+", " ", text.lower()).split()


def edit_distance(left: list[str], right: list[str]) -> int:
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        next_row = [i]
        for j, b in enumerate(right, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = next_row
    return row[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    words = _words(reference)
    return edit_distance(words, _words(hypothesis)) / max(len(words), 1)


def _speaker_at(turns, instant: float):
    active = {turn.speaker for turn in turns if turn.start <= instant < turn.end}
    return frozenset(active)


def _max_assignment_score(weights: list[list[int]]) -> int:
    """Maximum one-to-one row/column score without a factorial search."""
    if not weights or not weights[0]:
        return 0
    if len(weights) > len(weights[0]):
        weights = [list(column) for column in zip(*weights)]
    rows, columns = len(weights), len(weights[0])
    scores = {0: 0}
    for column in range(columns):
        next_scores = dict(scores)  # This column may remain unmatched.
        for mask, score in scores.items():
            for row in range(rows):
                if mask & (1 << row):
                    continue
                next_mask = mask | (1 << row)
                next_scores[next_mask] = max(
                    next_scores.get(next_mask, 0), score + weights[row][column]
                )
        scores = next_scores
    return max(scores.values(), default=0)


def diarization_error_rate(reference, hypothesis, duration: float, frame=0.02) -> float:
    """Frame DER with optimal anonymous-speaker assignment and overlap support."""
    ref_ids = sorted({turn.speaker for turn in reference})
    hyp_ids = sorted({turn.speaker for turn in hypothesis})
    ref_index = {speaker: index for index, speaker in enumerate(ref_ids)}
    hyp_index = {speaker: index for index, speaker in enumerate(hyp_ids)}
    cooccurrence = [[0 for _ in hyp_ids] for _ in ref_ids]
    total_slots = reference_speech = 0
    count = max(1, int(duration / frame + 0.5))
    for index in range(count):
        instant = (index + 0.5) * frame
        expected = _speaker_at(reference, instant)
        actual = _speaker_at(hypothesis, instant)
        reference_speech += len(expected)
        total_slots += max(len(expected), len(actual))
        for ref_speaker in expected:
            for hyp_speaker in actual:
                cooccurrence[ref_index[ref_speaker]][hyp_index[hyp_speaker]] += 1
    correct = _max_assignment_score(cooccurrence)
    return (total_slots - correct) / max(reference_speech, 1)


def _load_rttm(path: Path) -> list[meetings.DiarizationTurn]:
    labels = {}
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 8 or fields[0] != "SPEAKER":
            raise ValueError(f"Invalid RTTM line: {line}")
        label = fields[7]
        speaker = labels.setdefault(label, len(labels))
        start, duration = float(fields[3]), float(fields[4])
        turns.append(meetings.DiarizationTurn(start, start + duration, speaker))
    return turns


def _load_reference(args):
    if args.reference:
        data = json.loads(args.reference.read_text(encoding="utf-8"))
        segments = data.get("segments", [])
        labels = {}
        turns = []
        text = data.get("text") or " ".join(
            str(item.get("text", "")) for item in segments
        )
        for item in segments:
            label = str(item["speaker"])
            speaker = labels.setdefault(label, len(labels))
            turns.append(
                meetings.DiarizationTurn(
                    float(item["start"]), float(item["end"]), speaker
                )
            )
        attributed = " ".join(
            f"Speaker {labels[str(item['speaker'])] + 1} {item.get('text', '')}"
            for item in segments
        )
        return text, turns, attributed
    return args.text.read_text(encoding="utf-8"), _load_rttm(args.rttm), None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reference", type=Path, help="JSON with text/segments")
    source.add_argument("--text", type=Path, help="plain reference transcript")
    parser.add_argument("--rttm", type=Path, help="required with --text")
    parser.add_argument("--expected-speakers", type=int)
    parser.add_argument(
        "--model-id", help="cached/bundled Parakeet model id to benchmark"
    )
    args = parser.parse_args()
    if args.text and not args.rttm:
        parser.error("--text requires --rttm")
    if args.model_id:
        config.MODEL_ID = args.model_id

    from .diarizer import Diarizer
    from .transcriber import Transcriber, read_wav_mono_f32

    reference_text, reference_turns, reference_attributed = _load_reference(args)
    audio = read_wav_mono_f32(args.audio)
    duration = len(audio) / config.SAMPLE_RATE
    started = time.perf_counter()
    transcriber = Transcriber()
    asr_started = time.perf_counter()
    result = transcriber.transcribe_long(audio)
    asr_seconds = time.perf_counter() - asr_started
    diar_started = time.perf_counter()
    turns = Diarizer(args.expected_speakers).diarize(audio)
    diarization_seconds = time.perf_counter() - diar_started
    segments = meetings.align_speakers(result.sentences, turns)
    hypothesis = " ".join(segment.text for segment in segments)
    output = {
        "audio": str(args.audio),
        "duration_seconds": round(duration, 3),
        "word_error_rate": word_error_rate(reference_text, hypothesis),
        "diarization_error_rate": diarization_error_rate(
            reference_turns, turns, duration
        ),
        "speaker_count_error": abs(
            len({turn.speaker for turn in reference_turns})
            - len({turn.speaker for turn in turns})
        ),
        "attributed_word_error_rate": (
            word_error_rate(
                reference_attributed,
                " ".join(f"{segment.speaker} {segment.text}" for segment in segments),
            )
            if reference_attributed is not None
            else None
        ),
        "asr_seconds": round(asr_seconds, 3),
        "diarization_seconds": round(diarization_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "real_time_factor": round(
            (asr_seconds + diarization_seconds) / max(duration, 0.001), 4
        ),
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024, 1
        ),
        "transcript": hypothesis,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
