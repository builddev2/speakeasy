"""Default size, minimum size and frame-autosave name of each small web
window. The minimum is the smallest size at which the page does not clip
(checked with frontend/scripts/overflow-check.js). Meetings sizes itself
from the screen in meetings_window.py."""

from typing import NamedTuple


class WindowSize(NamedTuple):
    default: tuple[float, float]
    minimum: tuple[float, float]
    autosave: str


SIZES = {
    "training": WindowSize((640, 440), (560, 400), "SpeakeasyTrainingFrame"),
    "diagnostic": WindowSize((660, 600), (560, 520), "SpeakeasyDiagnosticFrame"),
}
