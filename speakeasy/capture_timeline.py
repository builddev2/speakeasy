"""Per-track timeline error: does a track's sample count keep up with its clock?

Meeting tracks are aligned once by their first-buffer times and then treated
as continuous. If a track silently loses (or gains) samples after that, every
later sample sits at the wrong place on the shared timeline. For each buffer:

    error = (buffer time - first buffer time) - frames before it / rate

Positive error means samples are missing (the buffer arrived later than its
sample count says it should). Callback scheduling only ever adds delay, so
the minimum over each window is the true offset; the series of window minima
shows a step (lost samples) versus a ramp (clock drift).

Numbers only — no audio, device or app names — so the summary may go into
capture health.
"""

import math

_WINDOW_SECONDS = 30.0
# Three hours of 30 s windows (config.MEETING_MAX_SECONDS).
_MAX_WINDOWS = 360


def _ms(seconds: float) -> int:
    return int(math.floor(seconds * 1000 + 0.5))


class CaptureTimeline:
    def __init__(
        self,
        rate: float,
        *,
        window_seconds: float = _WINDOW_SECONDS,
        max_windows: int = _MAX_WINDOWS,
    ) -> None:
        self._rate = float(rate)
        self._window_seconds = window_seconds
        self._max_windows = max_windows
        self._origin: float | None = None
        self._observations = 0
        self._window: int | None = None
        self._window_min = 0.0
        self._window_max = 0.0
        self._series: list[float] = []
        self._max_jitter = 0.0
        self._last_error: float | None = None
        self._max_jump = 0.0
        self._max_jump_at = 0.0

    def observe(self, time_seconds: float, frames_before: int) -> None:
        position = frames_before / self._rate
        if self._origin is None:
            self._origin = time_seconds - position
        error = time_seconds - self._origin - position
        self._observations += 1
        if self._last_error is not None:
            jump = error - self._last_error
            if abs(jump) > abs(self._max_jump):
                self._max_jump = jump
                self._max_jump_at = position
        self._last_error = error
        window = int(position // self._window_seconds)
        if window != self._window:
            self._close_window()
            self._window = window
            self._window_min = self._window_max = error
        else:
            self._window_min = min(self._window_min, error)
            self._window_max = max(self._window_max, error)

    def _close_window(self) -> None:
        if self._window is None:
            return
        self._max_jitter = max(self._max_jitter, self._window_max - self._window_min)
        if len(self._series) < self._max_windows:
            self._series.append(self._window_min)

    def summary(self) -> dict:
        series = list(self._series)
        jitter = self._max_jitter
        if self._window is not None:
            jitter = max(jitter, self._window_max - self._window_min)
            if len(series) < self._max_windows:
                series.append(self._window_min)
        step, step_at = 0.0, None
        for index in range(1, len(series)):
            delta = series[index] - series[index - 1]
            if abs(delta) > abs(step):
                step, step_at = delta, index * self._window_seconds
        return {
            "observations": self._observations,
            "error_ms": _ms(series[-1]) if series else None,
            "min_ms": _ms(min(series)) if series else None,
            "max_ms": _ms(max(series)) if series else None,
            "step_ms": _ms(step),
            "step_at_s": step_at,
            "max_jump_ms": _ms(self._max_jump),
            "max_jump_at_s": round(self._max_jump_at, 2),
            "max_jitter_ms": _ms(jitter),
            "series_ms": [_ms(value) for value in series],
        }
