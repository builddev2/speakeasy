"""The test session can never reach the real home folder, even from a thread
that outlives its test (10 Oct 2026: gated engine jobs saved 12 fake meetings
into the real library after isolated_home had restored HOME)."""

import os
import pwd
from pathlib import Path

from speakeasy import settings

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_library_stays_out_of_real_home_after_test_teardown(monkeypatch):
    # undo() is exactly what isolated_home's teardown does; a leaked worker
    # resolves the library path in this state.
    monkeypatch.undo()
    assert not Path.home().is_relative_to(REAL_HOME)
    assert not settings.library_path().is_relative_to(REAL_HOME)
