"""Enforce the offline rule: only speakeasy/model_update.py may touch the network."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "speakeasy"
ALLOWED = PKG / "model_update.py"
BANNED_MODULES = {"urllib.request", "http.client", "socket", "requests", "httpx"}
CURL = "/usr/bin/curl"


def _hits(tree):
    """Yield (lineno, description) for each banned import or curl string."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in BANNED_MODULES or a.name.split(".")[0] in {"socket", "requests", "httpx"}:
                    yield node.lineno, f"import {a.name}"
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in BANNED_MODULES or node.module.split(".")[0] in {"socket", "requests", "httpx"}:
                yield node.lineno, f"from {node.module} import ..."
            for a in node.names:
                if f"{node.module}.{a.name}" in BANNED_MODULES:
                    yield node.lineno, f"from {node.module} import {a.name}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and CURL in node.value:
            yield node.lineno, f"string containing {CURL}"


def test_only_model_update_touches_the_network():
    problems = []
    for path in sorted(PKG.rglob("*.py")):
        if path == ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, what in _hits(tree):
            problems.append(f"{path.relative_to(ROOT)}:{lineno}: {what}")
    assert not problems, (
        "network access is allowed only in speakeasy/model_update.py:\n  "
        + "\n  ".join(problems)
    )


def test_guard_detects_the_forms_it_claims_to_catch():
    src = (
        "from urllib import request\nimport urllib.request as x\nimport socket\n"
        "from http.client import HTTPSConnection\nimport requests\nimport httpx\n"
        "a = '/usr/bin/curl'\n"
    )
    lines = sorted(n for n, _ in _hits(ast.parse(src)))
    assert lines == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    "module", ["speakeasy.transcriber", "speakeasy.engine", "speakeasy.dictation_stream"]
)
def test_runtime_modules_do_not_import_model_update(module, tmp_path):
    code = (
        f"import sys, importlib; importlib.import_module({module!r}); "
        "sys.exit(1 if 'speakeasy.model_update' in sys.modules else 0)"
    )
    import os
    env = {**os.environ, "HOME": str(tmp_path), "HF_HUB_OFFLINE": "1"}
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        f"importing {module} failed or pulled in speakeasy.model_update\n{r.stderr[-1500:]}"
    )
