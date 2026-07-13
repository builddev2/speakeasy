"""align_speakers: diarization turns × timed sentences → attributed segments.

Inputs are duck-typed stand-ins for parakeet_mlx's AlignedToken/Sentence —
only .start/.end/.duration/.text/.tokens are read.
"""

from dataclasses import dataclass, field

from speakeasy.meetings import DiarizationTurn, align_speakers


@dataclass
class Token:
    start: float
    end: float
    text: str = "w"

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    tokens: list = field(default_factory=list)


def sentence(start, end, text):
    # Evenly spread one token per word across the sentence's span.
    words = text.split()
    step = (end - start) / max(len(words), 1)
    tokens = [
        Token(start + i * step, start + (i + 1) * step, w)
        for i, w in enumerate(words)
    ]
    return Sentence(start, end, text, tokens)


def test_two_clean_turns_two_segments():
    sentences = [
        sentence(0.0, 5.0, "Good morning everyone."),
        sentence(6.0, 10.0, "Thanks for having me."),
    ]
    turns = [(0.0, 5.5, 7), (5.5, 10.5, 3)]
    segments = align_speakers(sentences, turns)
    assert [(s.speaker, s.text) for s in segments] == [
        ("Speaker 1", "Good morning everyone."),
        ("Speaker 2", "Thanks for having me."),
    ]


def test_speaker_numbering_follows_first_appearance():
    # Diarizer ids are arbitrary ints; whoever talks first is "Speaker 1".
    sentences = [sentence(0.0, 2.0, "First voice."), sentence(3.0, 5.0, "Second voice.")]
    turns = [(0.0, 2.5, 9), (2.5, 5.5, 0)]
    segments = align_speakers(sentences, turns)
    assert segments[0].speaker == "Speaker 1"
    assert segments[1].speaker == "Speaker 2"


def test_consecutive_same_speaker_sentences_merge():
    sentences = [
        sentence(0.0, 2.0, "One thought."),
        sentence(2.5, 4.0, "Another thought."),
        sentence(5.0, 7.0, "A reply."),
    ]
    turns = [(0.0, 4.5, 1), (4.5, 7.5, 2)]
    segments = align_speakers(sentences, turns)
    assert len(segments) == 2
    assert segments[0].text == "One thought. Another thought."
    assert segments[0].start == 0.0 and segments[0].end == 4.0


def test_sentence_majority_absorbs_boundary_jitter():
    # 4 of 5 tokens belong to speaker 1's turn; the last strays into
    # speaker 2's — the sentence still goes to speaker 1.
    s = sentence(0.0, 5.0, "a b c d e")
    turns = [(0.0, 4.2, 1), (4.2, 9.0, 2)]
    segments = align_speakers([s], turns)
    assert len(segments) == 1
    assert segments[0].speaker == "Speaker 1"


def test_token_in_distant_gap_inherits_previous_speaker():
    # Second sentence sits 3s from any turn: keep the previous speaker
    # rather than jumping to a far-away turn.
    sentences = [
        sentence(0.0, 2.0, "Hello there."),
        sentence(5.0, 6.0, "Um."),
    ]
    turns = [(0.0, 2.0, 1), (9.0, 12.0, 2)]
    segments = align_speakers(sentences, turns)
    assert len(segments) == 1  # both attributed to Speaker 1, merged
    assert segments[0].speaker == "Speaker 1"


def test_token_in_small_gap_snaps_to_nearest_turn():
    sentences = [sentence(2.2, 3.0, "Right.")]
    turns = [(0.0, 2.0, 5), (8.0, 9.0, 6)]
    segments = align_speakers(sentences, turns)
    assert segments[0].speaker == "Speaker 1"  # nearest turn (0.7s away)


def test_no_turns_single_speaker():
    sentences = [sentence(0.0, 2.0, "Alone in the room.")]
    segments = align_speakers(sentences, [])
    assert [(s.speaker, s.text) for s in segments] == [
        ("Speaker 1", "Alone in the room.")
    ]


def test_empty_sentences():
    assert align_speakers([], [(0.0, 1.0, 1)]) == []


def test_blank_sentences_are_dropped():
    sentences = [sentence(0.0, 1.0, "Kept."), Sentence(1.0, 2.0, "   ", [])]
    segments = align_speakers(sentences, [(0.0, 2.0, 1)])
    assert len(segments) == 1
    assert segments[0].text == "Kept."


def test_sustained_speaker_change_splits_sentence():
    s = sentence(0.0, 4.0, "one two three four")
    turns = [(0.0, 2.0, 1), (2.0, 4.0, 2)]
    segments = align_speakers([s], turns)
    assert [(segment.speaker, segment.text) for segment in segments] == [
        ("Speaker 1", "one two"),
        ("Speaker 2", "three four"),
    ]


def test_unsplit_sentence_preserves_model_text():
    s = Sentence(
        0.0,
        2.0,
        "Hello, world!",
        [Token(0.0, 1.0, "Hello"), Token(1.0, 2.0, "world")],
    )
    assert align_speakers([s], [(0.0, 2.0, 1)])[0].text == "Hello, world!"


def test_overlap_metadata_only_marks_intersecting_segment():
    sentences = [
        sentence(0.0, 1.0, "Clear."),
        sentence(2.0, 3.0, "Overlapping."),
    ]
    turns = [
        DiarizationTurn(0.0, 1.0, 1),
        DiarizationTurn(2.0, 3.0, 1, overlap=True),
        DiarizationTurn(2.0, 3.0, 2, overlap=True),
    ]
    segments = align_speakers(sentences, turns)
    assert [segment.overlap for segment in segments] == [False, True]


def test_existing_reserved_profile_name_does_not_collide_with_anonymous_label():
    sentences = [
        sentence(0.0, 1.0, "Anonymous."),
        sentence(1.0, 2.0, "Identified."),
    ]
    turns = [
        DiarizationTurn(0.0, 1.0, 1),
        DiarizationTurn(1.0, 2.0, 2, profile_id="Speaker 1"),
    ]
    assert [segment.speaker for segment in align_speakers(sentences, turns)] == [
        "Speaker 2",
        "Speaker 1",
    ]
