"""Merge diarization clusters that are really one voice.

sherpa-onnx's threshold clustering over-splits real calls into dozens of
labels (see config.SPEAKER_MIN_TALK_SECONDS). Two passes, both on cluster
embeddings from the same bundled model voice profiles use:
1. clusters with little talk fold into the most similar real voice (or, with
   no usable embedding, the voice nearest in time);
2. with a cap (from the calendar), the most similar voices merge until the
   cap is met; a cap of 1 needs no embeddings at all.
The user's own speaker count bypasses all of this (engine._diarize_track)."""

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from . import config
from .meetings import DiarizationTurn


def _as_turn(t) -> DiarizationTurn:
    if isinstance(t, DiarizationTurn):
        return t
    return DiarizationTurn(float(t[0]), float(t[1]), int(t[2]))


def _talk(turns) -> dict[int, float]:
    talk: dict[int, float] = {}
    for t in turns:
        talk[t.speaker] = talk.get(t.speaker, 0.0) + max(0.0, t.end - t.start)
    return talk


def _unit(vector) -> np.ndarray | None:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm and np.all(np.isfinite(vector)) else None


def _gap(turns, a: int, b: int) -> float:
    """Shortest silence between any turn of `a` and any turn of `b`."""
    best = float("inf")
    for x in turns:
        if x.speaker != a:
            continue
        for y in turns:
            if y.speaker == b:
                best = min(best, max(0.0, y.start - x.end, x.start - y.end))
    return best


class _Groups:
    def __init__(self, turns, embeddings):
        self.turns = turns
        self.talk = _talk(turns)
        self.owner = {s: s for s in self.talk}
        self.vec = {s: u for s, e in embeddings.items() if s in self.talk
                    and (u := _unit(e)) is not None}

    def roots(self) -> list[int]:
        return sorted({self.owner[s] for s in self.talk})

    def root_talk(self, root: int) -> float:
        return sum(t for s, t in self.talk.items() if self.owner[s] == root)

    def similarity(self, a: int, b: int) -> float | None:
        if a in self.vec and b in self.vec:
            return float(np.dot(self.vec[a], self.vec[b]))
        return None

    def nearest_in_time(self, source: int, candidates) -> int:
        members = [s for s in self.talk if self.owner[s] == source]
        return min(candidates, key=lambda c: (
            min(_gap(self.turns, m, o) for m in members
                for o in self.talk if self.owner[o] == c),
            -self.root_talk(c), c))

    def merge(self, small: int, big: int) -> None:
        # The voice with more talk survives, so its label and id stay.
        if (self.root_talk(small), -small) > (self.root_talk(big), -big):
            small, big = big, small
        if small in self.vec and big in self.vec:
            w_s, w_b = self.root_talk(small), self.root_talk(big)
            merged = _unit(self.vec[small] * w_s + self.vec[big] * w_b)
            if merged is not None:
                self.vec[big] = merged
        self.vec.pop(small, None)
        for s, root in self.owner.items():
            if root == small:
                self.owner[s] = big


def merge_speakers(turns, embeddings, *, max_speakers, min_talk_seconds) -> list[DiarizationTurn]:
    turns = [_as_turn(t) for t in turns]
    if len({t.speaker for t in turns}) < 2:
        return turns
    g = _Groups(turns, embeddings)
    anchors = [s for s in g.roots() if g.talk[s] >= min_talk_seconds]
    if anchors:
        small = sorted((s for s in g.roots() if s not in anchors),
                       key=lambda s: (g.talk[s], s))
        for s in small:
            scored = [(sim, a) for a in anchors
                      if (sim := g.similarity(s, a)) is not None]
            target = (max(scored, key=lambda x: (x[0], -x[1]))[1] if scored
                      else g.nearest_in_time(s, anchors))
            g.merge(s, target)
    while max_speakers and len(g.roots()) > max_speakers:
        roots = g.roots()
        if max_speakers == 1:
            big = max(roots, key=lambda r: (g.root_talk(r), -r))
            for r in roots:
                if r != big:
                    g.merge(r, big)
            break
        pairs = [(sim, a, b) for i, a in enumerate(roots) for b in roots[i + 1:]
                 if (sim := g.similarity(a, b)) is not None]
        if pairs:
            _, a, b = max(pairs, key=lambda p: (p[0], -p[1], -p[2]))
        else:
            a = min(roots, key=lambda r: (g.root_talk(r), r))
            b = g.nearest_in_time(a, [r for r in roots if r != a])
        g.merge(a, b)
    merged = [replace(t, speaker=g.owner[t.speaker]) for t in turns]
    # Overlap marks concurrent *different* voices; merging can make two
    # overlapping turns the same voice, so recompute it.
    flagged = set()
    for i, first in enumerate(merged):
        for j in range(i + 1, len(merged)):
            second = merged[j]
            if second.start >= first.end:
                break
            if first.speaker != second.speaker and second.end > first.start:
                flagged.update((i, j))
    return [replace(t, overlap=i in flagged) for i, t in enumerate(merged)]


def _cluster_audio(samples, turns, speaker, max_seconds) -> np.ndarray:
    rate = config.SAMPLE_RATE
    chunks, used = [], 0.0
    for t in sorted((t for t in turns if t.speaker == speaker),
                    key=lambda t: (t.start - t.end, t.start)):
        take = min(t.end - t.start, max_seconds - used)
        if take <= 0:
            break
        chunk = samples[max(0, int(t.start * rate)):min(len(samples), int((t.start + take) * rate))]
        if len(chunk):
            chunks.append(chunk)
            used += take
    return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)


def tidy_speakers(samples, turns, embed: Callable, *, max_speakers=None,
                  min_talk_seconds=config.SPEAKER_MIN_TALK_SECONDS,
                  cancelled: Callable[[], bool] = lambda: False) -> list[DiarizationTurn]:
    """merge_speakers with embeddings computed only when a merge needs them.
    Never raises for a bad cluster: it just has no embedding."""
    turns = sorted((_as_turn(t) for t in turns), key=lambda t: (t.start, t.end))
    talk = _talk(turns)
    small_with_anchor = (any(t >= min_talk_seconds for t in talk.values())
                         and any(t < min_talk_seconds for t in talk.values()))
    over_cap = bool(max_speakers) and len(talk) > max_speakers
    if not small_with_anchor and not over_cap:
        return turns
    embeddings = {}
    if small_with_anchor or (over_cap and max_speakers > 1):
        for speaker in sorted(talk):
            if cancelled():
                return turns
            audio = _cluster_audio(samples, turns, speaker, config.SPEAKER_MERGE_EMBED_SECONDS)
            if not len(audio):
                continue
            try:
                embeddings[speaker] = embed(audio)
            except (ValueError, RuntimeError, OSError, TypeError):
                continue
    return merge_speakers(turns, embeddings, max_speakers=max_speakers,
                          min_talk_seconds=min_talk_seconds)
