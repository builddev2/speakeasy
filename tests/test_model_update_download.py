import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from collections import namedtuple

import pytest

from speakeasy import model_store, model_update, settings

SHA = "b" * 40
CFG_BYTES = b'{"cfg": true}'
SAFE_BYTES = b"S" * 5000
CFG_ID = hashlib.sha1(b"blob %d\0" % len(CFG_BYTES) + CFG_BYTES).hexdigest()
SAFE_ID = hashlib.sha256(SAFE_BYTES).hexdigest()
CONTENT = {"config.json": CFG_BYTES, "model.safetensors": SAFE_BYTES}
Usage = namedtuple("Usage", "total used free")


def make_result(cfg_id=CFG_ID, safe_id=SAFE_ID, total=None):
    return model_update.CheckResult(
        "available", SHA, "a" * 40, total or len(CFG_BYTES) + len(SAFE_BYTES), "m",
        {"config.json": cfg_id, "model.safetensors": safe_id},
        {"config.json": len(CFG_BYTES), "model.safetensors": len(SAFE_BYTES)})


class FakeProc:
    def __init__(self, rc=0, block=None, ignore_terminate=False):
        self.rc = rc
        self.block = block
        self.ignore = ignore_terminate
        self.terminated = False
        self.killed = False
        self.done = False

    def poll(self):
        if self.killed or (self.terminated and not self.ignore):
            return -15
        if self.block is not None and not self.block.is_set():
            return None
        return self.rc

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.poll()


class FakeRunner:
    def __init__(self, verify_rc=0, verify_out=b"", fail_file=None, fail_rc=0,
                 corrupt=None, block_on=None, ignore_terminate=False):
        self.verify_rc, self.verify_out = verify_rc, verify_out
        self.fail_file, self.fail_rc = fail_file, fail_rc
        self.corrupt = corrupt
        self.block_on = block_on
        self.ignore = ignore_terminate
        self.started = []
        self.runs = []
        self.procs = []
        self.partial_written = threading.Event()
        self.release = threading.Event()

    def run(self, argv, *, timeout):
        self.runs.append((list(argv), timeout))
        return self.verify_rc, self.verify_out

    def start(self, argv):
        self.started.append(list(argv))
        out = argv[argv.index("-o") + 1]
        name = out.rsplit("/", 1)[1]
        data = CONTENT[name]
        if self.corrupt == name:
            data = b"X" * len(data)
        if name == self.fail_file:
            data = data[: len(data) // 2]
            with open(out, "wb") as f:
                f.write(data)
            p = FakeProc(rc=self.fail_rc)
        elif name == self.block_on:
            with open(out, "wb") as f:
                f.write(data[: len(data) // 2])
            self.partial_written.set()
            p = FakeProc(rc=0, block=self.release, ignore_terminate=self.ignore)
        else:
            with open(out, "wb") as f:
                f.write(data)
            p = FakeProc()
        self.procs.append(p)
        return p


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    monkeypatch.setattr(model_update, "_POLL", 0.01)
    monkeypatch.setattr(model_update, "_KILL_AFTER", 0.05)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))
    yield
    assert not model_update._active.locked() if hasattr(model_update, "_active") else True


def run_download(runner, result=None):
    states = []
    d = model_update.Downloader(result or make_result(), runner,
                                lambda s, p, m: states.append((s, p, m)))
    d.start()
    d._thread.join(10)
    assert not d._thread.is_alive()
    return d, states


def root():
    return model_store.speech_models_dir()


def test_happy_path():
    model_store.write_active({"revision": "o", "dir": "rev-old", "status": "confirmed",
                              "previous": None})
    r = FakeRunner()
    d, states = run_download(r)
    names = [s for s, _, _ in states]
    assert names[-1] == "ready" and "verifying" in names
    assert states[-1][2] == "Update ready. It will be used the next time Speakeasy starts."
    assert model_store.read_active() == {"revision": SHA, "dir": f"rev-{SHA}",
                                         "status": "pending", "previous": "rev-old"}
    final = root() / f"rev-{SHA}"
    assert (final / "model.safetensors").read_bytes() == SAFE_BYTES
    src = json.loads((final / "source.json").read_text())
    assert src == {"model_id": "mlx-community/parakeet-tdt-0.6b-v2", "revision": SHA,
                   "files": {"config.json": CFG_ID, "model.safetensors": SAFE_ID},
                   "sizes": {"config.json": len(CFG_BYTES),
                             "model.safetensors": len(SAFE_BYTES)}}
    assert model_store.update_dir_if_valid() == final
    assert not (root() / f".staging-{SHA}").exists()
    # curl argv verbatim
    a = r.started[0]
    assert a[:15] == ["/usr/bin/curl", "-sS", "-f", "-L", "--proto", "=https",
                      "--proto-redir", "=https", "--connect-timeout", "10",
                      "--speed-limit", "1024", "--speed-time", "60", "-o"]
    assert a[-1] == ("https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v2/resolve/"
                     f"{SHA}/config.json")
    assert r.started[1][-1].endswith("/model.safetensors")
    # progress is monotone 0..1
    prog = [p for s, p, _ in states if s == "downloading"]
    assert prog == sorted(prog) and prog[-1] == pytest.approx(1.0)
    # verify argv
    argv, timeout = r.runs[0]
    assert timeout == 300
    assert argv == [sys.executable, "-m", "speakeasy", "--verify-model",
                    str(root() / f".staging-{SHA}")]


def test_verify_argv_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    r = FakeRunner()
    run_download(r)
    assert r.runs[0][0][:2] == [sys.executable, "--verify-model"]


def test_previous_none_without_pointer():
    run_download(FakeRunner())
    assert model_store.read_active()["previous"] is None


def test_existing_rev_dir_replaced():
    old = root() / f"rev-{SHA}"
    old.mkdir(parents=True)
    (old / "junk").write_text("x")
    _, states = run_download(FakeRunner())
    assert states[-1][0] == "ready"
    assert not (old / "junk").exists()
    assert (old / "model.safetensors").exists()


def test_cancel_mid_download():
    model_store.write_active({"revision": "o", "dir": "rev-old", "status": "confirmed",
                              "previous": None})
    before = model_store.read_active()
    r = FakeRunner(block_on="model.safetensors")
    states = []
    d = model_update.Downloader(make_result(), r, lambda s, p, m: states.append((s, p, m)))
    d.start()
    assert r.partial_written.wait(5)
    d.cancel()
    d._thread.join(10)
    assert states[-1] == ("cancelled", states[-1][1],
                          "Download cancelled. Your current model is unchanged.")
    assert r.procs[-1].terminated
    assert not (root() / f".staging-{SHA}").exists()
    assert not (root() / f"rev-{SHA}").exists()
    assert model_store.read_active() == before
    assert not r.runs


def test_cancel_kills_stubborn_curl():
    r = FakeRunner(block_on="config.json", ignore_terminate=True)
    d = model_update.Downloader(make_result(), r, lambda *a: None)
    d.start()
    assert r.partial_written.wait(5)
    d.cancel()
    d._thread.join(10)
    assert r.procs[-1].terminated and r.procs[-1].killed
    assert not (root() / f".staging-{SHA}").exists()


def test_hash_mismatch_safetensors():
    _, states = run_download(FakeRunner(corrupt="model.safetensors"))
    assert states[-1] == ("failed", states[-1][1],
                          "The download was damaged (checksum mismatch). "
                          "Your current model is unchanged.")
    assert not (root() / f".staging-{SHA}").exists()
    assert model_store.read_active() is None


def test_hash_mismatch_config():
    _, states = run_download(FakeRunner(corrupt="config.json"))
    assert states[-1][2].startswith("The download was damaged")


def test_verify_child_fails_with_last_line():
    r = FakeRunner(verify_rc=1, verify_out=b"noise\nshape mismatch in w\n\n")
    _, states = run_download(r)
    assert states[-1][0] == "failed"
    assert states[-1][2] == ("The new model didn't pass its check (shape mismatch in w). "
                             "Your current model is unchanged.")
    assert not (root() / f".staging-{SHA}").exists()
    assert not (root() / f"rev-{SHA}").exists()
    assert model_store.read_active() is None


def test_verify_reason_truncated_and_empty():
    _, states = run_download(FakeRunner(verify_rc=1, verify_out=b"y" * 500))
    assert "y" * 200 in states[-1][2] and "y" * 201 not in states[-1][2]
    _, states = run_download(FakeRunner(verify_rc=1, verify_out=b""))
    assert states[-1][2].startswith("The new model didn't pass its check (")


def test_verify_timeout():
    _, states = run_download(FakeRunner(verify_rc=28, verify_out=b""))
    # SubprocessRunner reports a timeout as rc 28 with no output
    assert states[-1][2] == ("The new model didn't pass its check (timed out). "
                             "Your current model is unchanged.")


def test_low_disk(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 0, 3_000_000_000))
    r = FakeRunner()
    _, states = run_download(r, make_result(total=2_470_000_000))
    assert states == [("failed", 0.0, "Not enough free disk space: the update needs about "
                       "3.5 GB free (2.5 GB download plus room to verify).")]
    assert not r.started
    assert not (root() / f".staging-{SHA}").exists()


def test_low_disk_creates_models_dir_first(monkeypatch):
    seen = []
    monkeypatch.setattr(shutil, "disk_usage", lambda p: seen.append(p.is_dir()) or
                        Usage(1, 0, 10**12))
    run_download(FakeRunner())
    assert seen == [True]


def test_stale_staging_removed_before_disk_check(monkeypatch):
    stale = root() / f".staging-{SHA}"
    stale.mkdir(parents=True)
    (stale / "model.safetensors").write_bytes(b"x" * 1000)
    seen = []
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: seen.append(stale.exists()) or Usage(1, 0, 10**12))
    run_download(FakeRunner())
    assert seen == [False]


def test_second_start_raises_and_lock_released():
    r = FakeRunner(block_on="model.safetensors")
    d = model_update.Downloader(make_result(), r, lambda *a: None)
    d.start()
    assert r.partial_written.wait(5)
    with pytest.raises(ValueError, match="A model download is already in progress."):
        model_update.Downloader(make_result(), FakeRunner(), lambda *a: None).start()
    r.release.set()
    d._thread.join(10)
    # released on success
    _, states = run_download(FakeRunner())
    assert states[-1][0] == "ready"


def test_lock_released_after_failure():
    run_download(FakeRunner(corrupt="model.safetensors"))
    _, states = run_download(FakeRunner())
    assert states[-1][0] == "ready"


def test_lock_released_when_on_state_raises():
    def bad(*a):
        raise RuntimeError("ui gone")
    d = model_update.Downloader(make_result(), FakeRunner(), bad)
    d.start()
    d._thread.join(10)
    _, states = run_download(FakeRunner())
    assert states[-1][0] == "ready"


@pytest.mark.parametrize("rc", [6, 7, 28])
def test_curl_offline_codes(rc):
    r = FakeRunner(fail_file="model.safetensors", fail_rc=rc)
    _, states = run_download(r)
    assert states[-1] == ("offline", states[-1][1], model_update._OFFLINE_MSG)
    assert not (root() / f".staging-{SHA}").exists()
    assert model_store.read_active() is None


def test_curl_other_failure():
    _, states = run_download(FakeRunner(fail_file="config.json", fail_rc=22))
    assert states[-1][0] == "failed"
    assert states[-1][2] == "The download didn't finish (curl exit 22). Your current model is unchanged."
    assert not (root() / f".staging-{SHA}").exists()


def test_short_file_fails_even_if_curl_ok():
    r = FakeRunner(fail_file="config.json", fail_rc=0)
    _, states = run_download(r)
    assert states[-1][0] == "failed"


def test_cleanup_does_not_delete_active_download():
    r = FakeRunner(block_on="model.safetensors")
    d = model_update.Downloader(make_result(), r, lambda *a: None)
    d.start()
    assert r.partial_written.wait(5)
    model_store.cleanup_leftovers()
    assert (root() / f".staging-{SHA}").is_dir()
    r.release.set()
    d._thread.join(10)
    assert not model_store._in_use


def test_active_json_untouched_until_swap():
    model_store.write_active({"revision": "o", "dir": "rev-old", "status": "confirmed",
                              "previous": None})
    before = model_store.read_active()
    run_download(FakeRunner(verify_rc=1))
    assert model_store.read_active() == before


def test_subprocess_runner_merges_stderr(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 1, b"out", None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert model_update.SubprocessRunner().run(["x"], timeout=1) == (1, b"out")
    assert seen["stderr"] == subprocess.STDOUT and seen["stdout"] == subprocess.PIPE


def _active_pointer():
    model_store.write_active({"revision": "o", "dir": "rev-old", "status": "confirmed",
                              "previous": None})
    return model_store.read_active()


@pytest.mark.parametrize("what", ["replace", "rmtree"])
def test_swap_failure_leaves_pointer_untouched(monkeypatch, what):
    import os
    before = _active_pointer()
    final = root() / f"rev-{SHA}"
    if what == "rmtree":
        final.mkdir(parents=True)
        real = shutil.rmtree

        def boom(p, *a, **k):
            if str(p) == str(final):
                raise OSError("nope")
            return real(p, *a, **k)
        monkeypatch.setattr(shutil, "rmtree", boom)
    else:
        real = os.replace

        def boom(src, dst, *a, **k):
            if ".staging-" in str(src):
                raise OSError("nope")
            return real(src, dst, *a, **k)
        monkeypatch.setattr(os, "replace", boom)
    _, states = run_download(FakeRunner())
    assert states[-1][0] == "failed"
    assert states[-1][2] == "The update couldn't be installed. Your current model is unchanged."
    assert model_store.read_active() == before
    assert not (root() / f".staging-{SHA}").exists()


@pytest.mark.parametrize("bad", ["../../x", "abc", "B" * 40, "a" * 41, "", None])
def test_downloader_rejects_bad_revision(tmp_path, bad):
    sentinel = tmp_path / "models" / "keep"
    sentinel.mkdir(parents=True)
    res = model_update.CheckResult(
        "available", bad, "a" * 40, 100, "m",
        {"config.json": CFG_ID, "model.safetensors": SAFE_ID},
        {"config.json": len(CFG_BYTES), "model.safetensors": len(SAFE_BYTES)})
    r = FakeRunner()
    _, states = run_download(r, res)
    assert states[-1][0] == "failed"
    assert states[-1][2] == "The update couldn't be installed. Your current model is unchanged."
    assert not r.started and not r.runs
    assert sentinel.is_dir()
    assert not [p for p in root().iterdir()] if root().exists() else True


def test_previous_never_points_at_the_new_dir():
    final = root() / f"rev-{SHA}"
    final.mkdir(parents=True)
    (final / "x").write_text("old")
    model_store.write_active({"revision": SHA, "dir": f"rev-{SHA}", "status": "confirmed",
                              "previous": None})
    run_download(FakeRunner())
    assert model_store.read_active() == {"revision": SHA, "dir": f"rev-{SHA}",
                                         "status": "pending", "previous": None}
    model_store.confirm_loaded()
    assert (final / "model.safetensors").exists()
