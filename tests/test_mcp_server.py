"""JSON-RPC handling in-process, plus real subprocess round-trips over pipes.

Every subprocess gets HOME=<tmp> so settings.app_support_dir() — the
library and the last-used stamp — resolve inside the temp dir, never the
user's real Application Support folder.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from speakeasy import mcp_server
from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.mcp_tools import Tool, ToolError

REPO = Path(__file__).resolve().parents[1]


def _tool(run, name="t"):
    return {name: Tool(name, "test tool", {"type": "object"}, run)}


def _req(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# -- in-process ---------------------------------------------------------------

def test_initialize_negotiates_version():
    s = mcp_server.Server({})
    r = s.handle(_req("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "t", "version": "0"}}))
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert r["result"]["capabilities"] == {"tools": {}}
    assert r["result"]["serverInfo"] == {"name": "speakeasy", "version": "1.0.0"}
    r = s.handle(_req("initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == "2025-06-18"
    r = s.handle(_req("initialize", {}))
    assert r["result"]["protocolVersion"] == "2025-06-18"


def test_ping_list_and_unknown_method():
    s = mcp_server.Server(_tool(lambda a: {}))
    assert s.handle(_req("ping", id="abc")) == {"jsonrpc": "2.0", "id": "abc", "result": {}}
    tools = s.handle(_req("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["t"]
    assert s.handle(_req("resources/list"))["error"]["code"] == -32601


@pytest.mark.parametrize("line, code, rid", [
    (b"not json", -32700, None),
    (b"\xff\xfe", -32700, None),
    (b"[]", -32600, None),
    (b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', -32600, None),
    (b'"hello"', -32600, None),
    # JSON-RPC: echo the id whenever it can be read, so the client can match it.
    (b'{"jsonrpc":"1.0","id":1,"method":"ping"}', -32600, 1),
    (b'{"jsonrpc":"2.0","id":1,"method":5}', -32600, 1),
    (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600, None),
])
def test_malformed_messages(line, code, rid):
    r = mcp_server.Server({}).handle_line(line)
    assert r["error"]["code"] == code and r["id"] == rid


def test_notifications_and_responses_get_no_reply():
    s = mcp_server.Server({})
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/whatever"}) is None
    assert s.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None


def test_tools_call_param_errors():
    s = mcp_server.Server(_tool(lambda a: {}))
    assert s.handle(_req("tools/call", {"name": "nope"}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", {}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", {"name": "t", "arguments": []}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", "x"))["error"]["code"] == -32602


def test_tools_call_result_is_compact_json():
    value = {"a": 1, "b": [1, 2], "c": {"d": "é"}}
    s = mcp_server.Server(_tool(lambda a: value))
    text = s.handle(_req("tools/call", {"name": "t", "arguments": {}}))["result"]["content"][0]["text"]
    assert ", " not in text and ": " not in text
    assert json.loads(text) == value


def test_tools_call_success_and_on_call():
    calls = []
    s = mcp_server.Server(_tool(lambda a: {"echo": a}), on_call=lambda: calls.append(1))
    r = s.handle(_req("tools/call", {"name": "t", "arguments": {"x": "é"}}))["result"]
    assert r["isError"] is False
    assert json.loads(r["content"][0]["text"]) == {"echo": {"x": "é"}}
    assert calls == [1]
    r = s.handle(_req("tools/call", {"name": "t"}))["result"]  # arguments optional
    assert json.loads(r["content"][0]["text"]) == {"echo": {}}


def test_on_call_failure_never_fails_the_call():
    def boom():
        raise OSError("disk full")
    s = mcp_server.Server(_tool(lambda a: {"ok": 1}), on_call=boom)
    assert s.handle(_req("tools/call", {"name": "t"}))["result"]["isError"] is False


@pytest.mark.parametrize("exc, text", [
    (ToolError("limit must be a whole number."), "limit must be a whole number."),
    (MeetingNotFound("20200101-000000-abcd"), "No meeting with id 20200101-000000-abcd."),
    (ValueError("Summary is longer than 20,000 characters."),
     "Summary is longer than 20,000 characters."),
    (sqlite3.OperationalError("database is locked"),
     "The meeting library is busy. Try again in a moment."),
    (RuntimeError("/Users/secret/path leaked"), "Speakeasy hit an internal error."),
])
def test_tool_errors_are_safe_results(exc, text, capsys):
    def run(args):
        raise exc
    r = mcp_server.Server(_tool(run)).handle(_req("tools/call", {"name": "t"}))["result"]
    assert r == {"content": [{"type": "text", "text": text}], "isError": True}
    assert "/Users/secret" not in capsys.readouterr().err


def test_busy_library_then_next_call_works(library_path, monkeypatch):
    # WAL readers never block, so "busy" means a write (save_notes) while
    # another connection (the app mid-import) holds the write lock.
    from speakeasy import meeting_store
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "x")],
                                      duration_seconds=1.0,
                                      started_at=datetime(2026, 9, 24, 10, tzinfo=timezone.utc)))
    real_connect = meeting_store.connect

    def quick_connect(path=None):  # the real 5 s busy timeout would slow the suite
        conn = real_connect(path)
        conn.execute("PRAGMA busy_timeout = 50")
        return conn
    monkeypatch.setattr(meeting_store, "connect", quick_connect)
    s = mcp_server.Server(mcp_server.build_tools(lib))
    call = _req("tools/call", {"name": "save_notes", "arguments": {"id": mid, "summary": "S"}})
    blocker = sqlite3.connect(library_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        r = s.handle(call)["result"]
        assert r == {"content": [{"type": "text", "text":
                     "The meeting library is busy. Try again in a moment."}], "isError": True}
        assert s.handle(_req("tools/call", {"name": "list_meetings"}))["result"]["isError"] is False
    finally:
        blocker.rollback()
        blocker.close()
    assert s.handle(call)["result"]["isError"] is False


def test_serve_skips_blank_lines_and_stops_at_eof(tmp_path):
    import io
    out = io.BytesIO()
    inp = io.BytesIO(b'\n  \n{"jsonrpc":"2.0","id":1,"method":"ping"}\ngarbage\n')
    mcp_server.serve(inp, out, mcp_server.Server({}))
    lines = out.getvalue().decode().splitlines()
    assert [json.loads(l).get("result", "err") for l in lines] == [{}, "err"]


@pytest.mark.parametrize("name", [["list_meetings"], {"a": 1}, 5, None])
def test_non_string_tool_name_is_invalid_params(name):
    s = mcp_server.Server(_tool(lambda a: {}))
    r = s.handle(_req("tools/call", {"name": name}))
    assert r["error"]["code"] == -32602


def test_deeply_nested_json_is_a_parse_error():
    r = mcp_server.Server({}).handle_line(b"[" * 100000)
    assert r["error"]["code"] == -32700


def test_serve_survives_lone_surrogate_and_internal_errors(capsys):
    import io
    out = io.BytesIO()
    inp = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"\\ud800"}\n'
                     b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    mcp_server.serve(inp, out, mcp_server.Server({}))
    lines = [json.loads(l) for l in out.getvalue().decode("ascii").splitlines()]
    assert lines[0]["error"]["code"] == -32601
    assert lines[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}

    class Boom(mcp_server.Server):
        def handle_line(self, line):
            if b"boom" in line:
                raise KeyError("secret meeting text")
            return super().handle_line(line)
    out = io.BytesIO()
    inp = io.BytesIO(b'boom\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    mcp_server.serve(inp, out, Boom({}))
    lines = [json.loads(l) for l in out.getvalue().decode("ascii").splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32603, "message": "Internal error"}}
    assert lines[1]["result"] == {}
    err = capsys.readouterr().err
    assert "mcp: internal error: KeyError" in err and "secret" not in err


# -- subprocess over pipes ------------------------------------------------------

def _env(home):
    env = {k: v for k, v in os.environ.items() if k not in ("HOME", "PYTHONPATH")}
    env.update(HOME=str(home), PYTHONPATH=str(REPO))
    return env


def _library_in(home) -> MeetingLibrary:
    path = home / "Library" / "Application Support" / "Speakeasy" / "library.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return MeetingLibrary(path)


def _run(home, messages, argv=None, raw=b""):
    data = raw + b"".join(json.dumps(m).encode() + b"\n" for m in messages)
    proc = subprocess.run(argv or [sys.executable, "-m", "speakeasy", "--mcp"],
                          input=data, capture_output=True, env=_env(home), cwd=REPO,
                          timeout=60)
    replies = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    return proc, {r.get("id"): r for r in replies}, replies


def _session(*calls):
    msgs = [_req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "pytest", "version": "0"}}, id=0),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _req("tools/list", id="list")]
    for i, (name, args) in enumerate(calls, start=1):
        msgs.append(_req("tools/call", {"name": name, "arguments": args}, id=i))
    return msgs


def _payload(reply):
    assert reply["result"]["isError"] is False, reply
    return json.loads(reply["result"]["content"][0]["text"])


def test_subprocess_round_trip_every_tool(tmp_path):
    lib = _library_in(tmp_path)
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "the budget is agreed")],
        duration_seconds=60.0, title="Budget",
        started_at=datetime(2026, 9, 24, 10, tzinfo=timezone(timedelta(hours=-7)))))
    proc, by_id, _ = _run(tmp_path, _session(
        ("list_meetings", {}), ("get_meeting", {"id": mid}),
        ("search_meetings", {"query": "budget"}), ("get_transcript", {"id": mid}),
        ("get_calendar", {"from": "2026-09-01", "to": "2026-09-30"}),
        ("list_tags", {}), ("list_people", {}),
        ("save_notes", {"id": mid, "summary": "Agreed", "tags": ["budget"]}),
        ("get_meeting", {"id": "20200101-000000-abcd"}),
        ("list_meetings", {"title": "BUDGET"}), ("list_meetings", {"title": "todd"}),
    ))
    assert proc.returncode == 0, proc.stderr
    assert by_id[0]["result"]["serverInfo"]["name"] == "speakeasy"
    assert len(by_id["list"]["result"]["tools"]) == 11
    assert _payload(by_id[1])["meetings"][0]["id"] == mid
    assert _payload(by_id[2])["title"] == "Budget"
    assert _payload(by_id[3])["results"][0]["meeting_id"] == mid
    assert _payload(by_id[4])["text"] == "[00:00:00] You: the budget is agreed"
    assert _payload(by_id[5]) == {"events": []}
    assert _payload(by_id[8])["summary"] == "Agreed"
    assert by_id[9]["result"]["isError"] is True
    assert [m["id"] for m in _payload(by_id[10])["meetings"]] == [mid]
    assert _payload(by_id[11])["meetings"] == []
    assert lib.get_meeting(mid).notes.updated_by == "claude"
    stamp = tmp_path / "Library/Application Support/Speakeasy/mcp_last_used"
    assert stamp.read_text().endswith("Z")


def test_subprocess_fresh_home_without_library(tmp_path):
    proc, by_id, _ = _run(tmp_path, _session(("list_meetings", {}),
                                             ("search_meetings", {"query": "x"})))
    assert proc.returncode == 0, proc.stderr
    assert _payload(by_id[1])["meetings"] == []
    assert _payload(by_id[2])["results"] == []


def test_subprocess_survives_a_messy_stream_and_exits_on_eof(tmp_path):
    proc, _, replies = _run(tmp_path, [_req("ping", id=7)],
                            raw=b"garbage\n\n\xff\xfe\n[1,2]\n")
    assert proc.returncode == 0
    assert [r.get("error", {}).get("code") for r in replies] == [-32700, -32700, -32600, None]
    assert replies[-1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_stray_output_in_a_tool_never_reaches_stdout(tmp_path):
    # A tool (or a C library) that prints must not corrupt the JSON stream:
    # both a Python print() and a raw write to fd 1 must land on stderr.
    code = (
        "import os\n"
        "from speakeasy import mcp_server, mcp_tools\n"
        "real = mcp_tools.build_tools\n"
        "def noisy(args):\n"
        "    print('stray print')\n"
        "    os.write(1, b'stray fd write\\n')\n"
        "    return {'ok': True}\n"
        "def build(lib):\n"
        "    tools = real(lib)\n"
        "    tools['noisy'] = mcp_tools.Tool('noisy', 'test', {'type': 'object'}, noisy)\n"
        "    return tools\n"
        "mcp_server.build_tools = build\n"
        "mcp_server.main()\n"
    )
    proc, by_id, replies = _run(tmp_path, _session(("noisy", {})),
                                argv=[sys.executable, "-c", code])
    assert proc.returncode == 0, proc.stderr
    assert _payload(by_id[1]) == {"ok": True}
    err = proc.stderr.decode()
    assert "stray print" in err and "stray fd write" in err
    assert b"stray" not in proc.stdout


def test_mcp_mode_imports_nothing_heavy(tmp_path):
    code = (
        "import sys\n"
        "sys.argv = ['speakeasy', '--mcp']\n"
        "from speakeasy.__main__ import main\n"
        "main()\n"
        # Through the real entry point, so a heavy import added to __main__ is caught too.
        "heavy = [m for m in sys.modules if m.split('.')[0] in ('AppKit', 'Foundation', 'objc',"
        " 'mlx', 'numpy', 'sherpa_onnx', 'sounddevice') or m == 'speakeasy.engine'"
        " or m == 'speakeasy.ui' or m.startswith('speakeasy.ui.')]\n"
        "sys.stderr.write('HEAVY=' + ','.join(heavy))\n"
    )
    proc, _, _ = _run(tmp_path, _session(("list_meetings", {})),
                      argv=[sys.executable, "-c", code])
    assert proc.stderr.decode().rstrip().endswith("HEAVY="), proc.stderr.decode()
