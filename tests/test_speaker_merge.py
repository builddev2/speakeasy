import numpy as np

from speakeasy.meetings import DiarizationTurn as T
from speakeasy.speaker_merge import merge_speakers, tidy_speakers


def v(*xs):
    return np.array(xs, dtype=np.float32)


def speakers(turns):
    return [t.speaker for t in turns]


def test_small_cluster_joins_most_similar_voice():
    turns = [T(0, 40, 0), T(40, 100, 1), T(100, 140, 0), T(140, 143, 2)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(0, 1), 2: v(0.1, 1)},
                         max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 0, 1]


def test_all_clusters_small_are_left_alone():
    turns = [T(0, 2, 0), T(2, 4, 1), T(4, 6, 2)]
    out = merge_speakers(turns, {}, max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 2]


def test_small_cluster_without_embedding_joins_nearest_in_time():
    turns = [T(0, 40, 0), T(50, 100, 1), T(100.5, 102, 2)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(0, 1)},
                         max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 1]


def test_cap_merges_most_similar_pairs_into_the_larger_voice():
    turns = [T(0, 40, 0), T(40, 70, 1), T(70, 90, 2), T(90, 125, 3)]
    emb = {0: v(1, 0, 0), 1: v(0.9, 0.1, 0), 2: v(0, 0, 1), 3: v(0, 0.1, 0.9)}
    out = merge_speakers(turns, emb, max_speakers=2, min_talk_seconds=15)
    assert speakers(out) == [0, 0, 3, 3]


def test_cap_of_one_needs_no_embeddings():
    turns = [T(0, 20, 5), T(20, 60, 7), T(60, 61, 9)]
    out = merge_speakers(turns, {}, max_speakers=1, min_talk_seconds=15)
    assert speakers(out) == [7, 7, 7]


def test_cap_above_cluster_count_changes_nothing():
    turns = [T(0, 20, 0), T(20, 40, 1)]
    assert speakers(merge_speakers(turns, {}, max_speakers=3, min_talk_seconds=15)) == [0, 1]


def test_overlap_is_recomputed_after_merge():
    turns = [T(0, 20, 0, overlap=True), T(19, 21, 1, overlap=True)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(1, 0.01)},
                         max_speakers=None, min_talk_seconds=15)
    assert [(t.speaker, t.overlap) for t in out] == [(0, False), (0, False)]


def test_tidy_accepts_tuples_and_skips_embedding_when_nothing_to_merge():
    calls = []
    out = tidy_speakers(np.zeros(16000 * 4, dtype=np.float32),
                        [(0.0, 0.8, 4), (1.0, 1.8, 9)], lambda s: calls.append(s))
    assert speakers(out) == [4, 9] and calls == []


def test_tidy_survives_embedding_failure():
    def embed(samples):
        raise ValueError("too short")
    samples = np.zeros(16000 * 50, dtype=np.float32)
    out = tidy_speakers(samples, [T(0, 20, 0), T(20, 40, 1), T(40, 41, 2)], embed)
    assert speakers(out) == [0, 1, 1]   # time fallback: 2 touches 1


def test_tidy_stops_embedding_when_cancelled():
    calls = []
    samples = np.zeros(16000 * 50, dtype=np.float32)
    out = tidy_speakers(samples, [T(0, 20, 0), T(20, 40, 1), T(40, 41, 2)],
                        lambda s: calls.append(1) or v(1, 0), cancelled=lambda: True)
    assert calls == [] and len(out) == 3
