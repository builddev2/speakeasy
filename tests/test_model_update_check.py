import json
import subprocess

import pytest

from speakeasy import model_source, model_update

REV_OLD = "a" * 40
REV_NEW = "b" * 40
CFG = "c" * 40
SAFE = "d" * 64
URL = ("https://huggingface.co/api/models/mlx-community/"
       "parakeet-tdt-0.6b-v2/revision/main?blobs=true")


def hub(sha=REV_OLD, cfg=CFG, safe=SAFE, safe_size=2_470_000_000, cfg_size=30_000):
    return {
        "sha": sha,
        "siblings": [
            {"rfilename": "README.md", "size": 10, "blobId": "e" * 40},
            {"rfilename": "config.json", "size": cfg_size, "blobId": cfg},
            {"rfilename": "model.safetensors", "size": safe_size, "blobId": "f" * 40,
             "lfs": {"sha256": safe, "size": safe_size}},
        ],
    }


class FakeRunner:
    def __init__(self, rc=0, body=None, raw=None, exc=None):
        self.rc = rc
        self.out = raw if raw is not None else json.dumps(body).encode()
        self.exc = exc
        self.calls = []

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if self.exc:
            raise self.exc
        return self.rc, self.out

    def start(self, argv):
        raise AssertionError("not used")


@pytest.fixture
def current(monkeypatch):
    ident = {"model_id": "x", "revision": REV_OLD,
             "files": {"config.json": CFG, "model.safetensors": SAFE},
             "origin": "bundled"}
    monkeypatch.setattr(model_source, "active_identity", lambda: ident)
    return ident


def test_up_to_date(current):
    r = model_update.check(FakeRunner(body=hub()))
    assert r.state == "up_to_date"
    assert r.message == "Speech model is up to date (revision aaaaaaa)."
    assert r.remote_revision == REV_OLD and r.current_revision == REV_OLD


def test_same_files(current):
    r = model_update.check(FakeRunner(body=hub(sha=REV_NEW)))
    assert r.state == "same_files"
    assert r.message == ("Up to date. The repository changed (aaaaaaa → bbbbbbb), "
                         "but the model files are the same.")


def test_available_weights(current):
    r = model_update.check(FakeRunner(body=hub(sha=REV_NEW, safe="9" * 64)))
    assert r.state == "available"
    assert r.download_bytes == 2_470_000_000 + 30_000
    assert r.message == ("A newer speech model is available "
                         "(revision bbbbbbb, 2.5 GB download).")


def test_available_config_only(current):
    r = model_update.check(FakeRunner(body=hub(sha=REV_NEW, cfg="9" * 40)))
    assert r.state == "available"


def test_size_falls_back_to_size_field(current):
    body = hub(sha=REV_NEW, safe="9" * 64)
    del body["siblings"][2]["lfs"]["size"]
    assert model_update.check(FakeRunner(body=body)).download_bytes == 2_470_030_000


@pytest.mark.parametrize("rc", [6, 7, 28])
def test_offline(current, rc):
    r = model_update.check(FakeRunner(rc=rc, raw=b""))
    assert r.state == "offline"
    assert r.message == ("Couldn't reach Hugging Face. Check your internet connection "
                         "and try again. Speakeasy works offline as usual.")


def test_other_exit_code_is_error(current):
    r = model_update.check(FakeRunner(rc=22, raw=b""))
    assert r.state == "error"
    assert r.message.startswith("Couldn't check for a model update (")
    assert r.message.endswith("). Speakeasy works offline as usual.")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"{}", b""])
def test_bad_json_is_error(current, raw):
    assert model_update.check(FakeRunner(raw=raw)).state == "error"


def test_missing_siblings_is_error(current):
    body = hub()
    body["siblings"] = [s for s in body["siblings"] if s["rfilename"] != "config.json"]
    assert model_update.check(FakeRunner(body=body)).state == "error"
    body = hub()
    body["siblings"] = [s for s in body["siblings"] if s["rfilename"] != "model.safetensors"]
    assert model_update.check(FakeRunner(body=body)).state == "error"


def test_missing_sha_is_error(current):
    body = hub()
    del body["sha"]
    assert model_update.check(FakeRunner(body=body)).state == "error"


def test_missing_lfs_sha_is_error(current):
    body = hub()
    del body["siblings"][2]["lfs"]
    assert model_update.check(FakeRunner(body=body)).state == "error"


def test_unknown_current_none(monkeypatch):
    monkeypatch.setattr(model_source, "active_identity", lambda: None)
    r = model_update.check(FakeRunner(body=hub()))
    assert r.state == "unknown_current"
    assert r.message == "Can't tell which model is installed, so no update can be offered."


@pytest.mark.parametrize("ident", [{}, {"revision": REV_OLD}, {"revision": REV_OLD, "files": "x"},
                                   {"revision": REV_OLD, "files": {"config.json": CFG}}])
def test_unknown_current_malformed(monkeypatch, ident):
    monkeypatch.setattr(model_source, "active_identity", lambda: ident)
    assert model_update.check(FakeRunner(body=hub())).state == "unknown_current"


def test_identity_raising_is_unknown(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(model_source, "active_identity", boom)
    assert model_update.check(FakeRunner(body=hub())).state == "unknown_current"


def test_argv_url_flags_and_single_host(current):
    fake = FakeRunner(body=hub())
    model_update.check(fake)
    argv, timeout = fake.calls[0]
    assert argv[0] == "/usr/bin/curl"
    assert argv[-1] == URL
    assert model_update.CHECK_URL == URL
    assert "--max-time" in argv and argv[argv.index("--max-time") + 1] == "15"
    assert "--connect-timeout" in argv
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--proto-redir") + 1] == "=https"
    assert [a for a in argv if "://" in a] == [URL]
    assert timeout > 15


def test_never_raises(current):
    assert model_update.check(FakeRunner(exc=RuntimeError("boom"))).state == "error"


def test_subprocess_runner_timeout_and_missing(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("curl", 1)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert model_update.SubprocessRunner().run(["x"], timeout=1)[0] == 28

    def missing(*a, **k):
        raise FileNotFoundError("curl")
    monkeypatch.setattr(subprocess, "run", missing)
    rc, _ = model_update.SubprocessRunner().run(["x"], timeout=1)
    assert rc not in (6, 7, 28)


def test_subprocess_runner_start(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw) or "proc")
    assert model_update.SubprocessRunner().start(["curl"]) == "proc"
    assert seen["kw"] == {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def test_format_gb():
    assert model_update.format_gb(2_470_000_000) == "2.5 GB"
    assert model_update.format_gb(0) == "0.0 GB"


def test_not_imported_by_runtime_modules():
    import pathlib
    for name in ("transcriber", "engine", "dictation_stream"):
        p = pathlib.Path(model_update.__file__).parent / f"{name}.py"
        if p.exists():
            assert "model_update" not in p.read_text()


def test_missing_safetensors_size_is_error(current):
    body = hub(sha=REV_NEW, safe="9" * 64)
    del body["siblings"][2]["lfs"]["size"]
    del body["siblings"][2]["size"]
    r = model_update.check(FakeRunner(body=body))
    assert r.state == "error" and "size" in r.message


def test_missing_config_size_is_error(current):
    body = hub(sha=REV_NEW, safe="9" * 64)
    del body["siblings"][1]["size"]
    r = model_update.check(FakeRunner(body=body))
    assert r.state == "error" and "size" in r.message


def test_remote_files_and_sizes(current):
    r = model_update.check(FakeRunner(body=hub(sha=REV_NEW, safe="9" * 64)))
    assert r.remote_files == {"config.json": CFG, "model.safetensors": "9" * 64}
    assert r.remote_sizes == {"config.json": 30_000, "model.safetensors": 2_470_000_000}
    for body in (hub(), hub(sha=REV_NEW)):
        r = model_update.check(FakeRunner(body=body))
        assert r.remote_files == {"config.json": CFG, "model.safetensors": SAFE}
        assert r.remote_sizes["config.json"] == 30_000
    for r in (model_update.check(FakeRunner(rc=28, raw=b"")), model_update.check(FakeRunner(raw=b"x"))):
        assert r.remote_files is None and r.remote_sizes is None


@pytest.mark.parametrize("sha", ["../../x", "abc", "B" * 40, "g" * 40])
def test_hostile_revision_is_an_error(current, sha):
    r = model_update.check(FakeRunner(body=hub(sha=sha)))
    assert r.state == "error"
    assert "unexpected revision in reply" in r.message
