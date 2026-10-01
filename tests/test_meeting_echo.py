"""remove_mic_echo: drop mic sentences that are system audio replayed
through the speakers, keep everything the user actually said.

Sentences are duck-typed like parakeet_mlx AlignedSentence: .start/.end/
.text/.tokens, where a token whose text starts with a space begins a word.
"""

from dataclasses import dataclass, field

from speakeasy import config
from speakeasy.meetings import remove_mic_echo


@dataclass
class Token:
    start: float
    end: float
    text: str

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    tokens: list = field(default_factory=list)


def sentence(start, end, text):
    words = text.split()
    step = (end - start) / max(len(words), 1)
    tokens = [
        Token(start + i * step, start + (i + 1) * step, w if i == 0 else f" {w}")
        for i, w in enumerate(words)
    ]
    return Sentence(start, end, text, tokens)


REMOTE = "we should ship the estimate by friday afternoon"


def test_bleed_copy_of_system_sentence_is_removed():
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.08, 13.08, REMOTE)]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert len(candidates) == 1 and candidates[0].removed
    assert candidates[0].coverage == 1.0
    assert abs(candidates[0].lag_seconds - 0.08) < 1e-6


def test_imperfect_echo_transcription_still_removed():
    # Mic ASR of bleed is quieter/reverberant: one word differs out of eight.
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.1, 13.1, "we should ship the estimate by friday afternoons")]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert candidates[0].coverage == 0.88


def test_own_speech_is_kept():
    system = [sentence(10.0, 13.0, REMOTE)]
    mine = sentence(14.0, 16.0, "sounds good I will draft it tonight")
    kept, candidates = remove_mic_echo([mine], system)
    assert kept == [mine]
    assert candidates == []


def test_repeat_after_remote_is_kept():
    # User repeats the remote's last words after they finish: same words,
    # but ~1.5 s later — a human repeat, not acoustic bleed.
    system = [sentence(10.0, 11.5, "forty hours total")]
    repeat = sentence(11.5, 13.0, "forty hours total")
    kept, candidates = remove_mic_echo([repeat], system)
    assert kept == [repeat]
    assert all(not c.removed for c in candidates)


def test_mixed_sentence_is_kept():
    # User talks over the bleed: half the mic words are theirs.
    system = [sentence(10.0, 13.0, REMOTE)]
    mixed = sentence(10.05, 13.05, "we should ship hang on let me check my calendar")
    kept, candidates = remove_mic_echo([mixed], system)
    assert kept == [mixed]
    assert candidates and not candidates[0].removed
    assert candidates[0].coverage < config.MEETING_ECHO_MIN_COVERAGE


def test_offsets_are_applied_before_comparing():
    # Track-local times differ by the first-buffer offset; on the shared
    # timeline the mic copy trails the system speech by 0.05 s.
    system = [sentence(9.75, 12.75, REMOTE)]          # + 0.25 offset → 10.0
    mic = [sentence(10.05, 13.05, REMOTE)]            # + 0.0 offset
    kept, _ = remove_mic_echo(mic, system, mic_offset=0.0, system_offset=0.25)
    assert kept == []
    kept, candidates = remove_mic_echo(mic, system)   # offsets ignored → lag 0.30
    assert kept == []                                 # still within the gate
    far = [sentence(8.0, 11.0, REMOTE)]               # mic trails by 2.05 s
    kept, _ = remove_mic_echo(mic, far, system_offset=0.0)
    assert kept == mic


def test_mic_leading_system_beyond_gate_is_kept():
    system = [sentence(10.5, 13.5, REMOTE)]
    mic = [sentence(10.0, 13.0, REMOTE)]              # mic 0.5 s *before* system
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == mic
    assert candidates[0].lag_seconds == -0.5


def test_short_sentence_needs_every_word():
    system = [sentence(5.0, 5.6, "okay yeah")]
    assert remove_mic_echo([sentence(5.05, 5.65, "okay yeah")], system)[0] == []
    partial = sentence(5.05, 5.65, "okay sure")
    assert remove_mic_echo([partial], system)[0] == [partial]


def test_subword_tokens_join_into_words():
    def subword(start, pieces):
        tokens = [Token(start + i * 0.1, start + (i + 1) * 0.1, p)
                  for i, p in enumerate(pieces)]
        return Sentence(start, start + 0.1 * len(pieces), "".join(pieces), tokens)
    system = [subword(3.0, ["est", "im", "ate", " by", " fri", "day"])]
    mic = [subword(3.05, ["est", "im", "ate", " by", " fri", "day"])]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert candidates[0].words == 3


def test_sentence_without_tokens_uses_text():
    system = [Sentence(4.0, 5.0, "ship it friday")]
    mic = [Sentence(4.1, 5.1, "ship it friday")]
    kept, _ = remove_mic_echo(mic, system)
    assert kept == []


def test_empty_inputs():
    mine = sentence(0.0, 1.0, "hello there")
    assert remove_mic_echo([], [sentence(0.0, 1.0, "x y")]) == ([], [])
    assert remove_mic_echo([mine], []) == ([mine], [])
    assert remove_mic_echo(None, None) == ([], [])


def test_thresholds_read_from_config_at_call_time(monkeypatch):
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.08, 13.08, REMOTE)]
    monkeypatch.setattr(config, "MEETING_ECHO_MAX_LAG_SECONDS", 0.05)
    assert remove_mic_echo(mic, system)[0] == mic


def test_system_offset_decides_the_result():
    # Raw system times are 1.0 s early; only the offset lines them up.
    system = [sentence(9.0, 12.0, REMOTE)]
    mic = [sentence(10.05, 13.05, REMOTE)]
    kept, candidates = remove_mic_echo(mic, system, system_offset=1.0)
    assert kept == []
    assert candidates[0].removed
    assert abs(candidates[0].lag_seconds - 0.05) < 1e-6
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == mic
    assert all(not c.removed for c in candidates)


def test_mic_offset_decides_the_result():
    # Raw mic times are 1.0 s late; only the (negative) offset fixes them.
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(11.05, 14.05, REMOTE)]
    kept, candidates = remove_mic_echo(mic, system, mic_offset=-1.0)
    assert kept == []
    assert candidates[0].removed
    assert abs(candidates[0].lag_seconds - 0.05) < 1e-6
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == mic
    assert all(not c.removed for c in candidates)


def test_full_coverage_repeat_beyond_lag_gate_is_kept():
    # Same three words, fully inside the search window, but 0.9 s late:
    # coverage 1.0 so only the lag gate can keep it.
    system = [sentence(10.0, 11.5, "forty hours total")]
    repeat = sentence(10.9, 12.4, "forty hours total")
    kept, candidates = remove_mic_echo([repeat], system)
    assert kept == [repeat]
    assert candidates[0].coverage == 1.0
    assert abs(candidates[0].lag_seconds - 0.9) < 1e-6
    assert not candidates[0].removed
