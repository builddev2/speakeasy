"""Pure-logic tests for the JS bridge: dispatch, eval queueing, formatting."""

import json
from collections import namedtuple

import pytest

from speakeasy.ui.webbridge import (
    BridgeDispatcher,
    EvalQueue,
    format_timestamp,
    segments_to_lines,
)

Seg = namedtuple("Seg", "speaker start end text")


def _parse_js(js):
    """Extract (kind, id, payload) from a _resolve/_reject JS string."""
    assert js.startswith("window.speakeasyBridge && window.speakeasyBridge.")
    body = js.split("window.speakeasyBridge.", 1)[1]
    kind, rest = body.split("(", 1)
    args = rest.rsplit(")", 1)[0]
    call_id, payload = args.split(",", 1)
    return kind, int(call_id), json.loads(payload)


class TestDispatcher:
    def test_routes_to_registered_handler_and_resolves(self):
        d = BridgeDispatcher()
        d.register("math.add", lambda params, respond: respond(params["a"] + params["b"]))
        sent = []
        d.dispatch({"id": 7, "method": "math.add", "params": {"a": 2, "b": 3}}, sent.append)
        kind, call_id, payload = _parse_js(sent[0])
        assert (kind, call_id, payload) == ("_resolve", 7, 5)

    def test_unknown_method_rejects(self):
        d = BridgeDispatcher()
        sent = []
        d.dispatch({"id": 1, "method": "nope", "params": {}}, sent.append)
        kind, call_id, payload = _parse_js(sent[0])
        assert kind == "_reject"
        assert call_id == 1
        assert "nope" in payload

    def test_handler_exception_rejects(self):
        d = BridgeDispatcher()

        def boom(params, respond):
            raise RuntimeError("kapow")

        d.register("boom", boom)
        sent = []
        d.dispatch({"id": 2, "method": "boom", "params": {}}, sent.append)
        kind, _, payload = _parse_js(sent[0])
        assert kind == "_reject"
        assert "kapow" in payload

    def test_handler_error_via_respond(self):
        d = BridgeDispatcher()
        d.register("bad", lambda params, respond: respond(error="not now"))
        sent = []
        d.dispatch({"id": 3, "method": "bad", "params": {}}, sent.append)
        kind, _, payload = _parse_js(sent[0])
        assert (kind, payload) == ("_reject", "not now")

    def test_deferred_respond(self):
        d = BridgeDispatcher()
        held = {}
        d.register("later", lambda params, respond: held.setdefault("respond", respond))
        sent = []
        d.dispatch({"id": 4, "method": "later", "params": {}}, sent.append)
        assert sent == []
        held["respond"]({"ok": True})
        kind, call_id, payload = _parse_js(sent[0])
        assert (kind, call_id, payload) == ("_resolve", 4, {"ok": True})

    def test_respond_twice_ignored(self):
        d = BridgeDispatcher()

        def double(params, respond):
            respond(1)
            respond(2)

        d.register("double", double)
        sent = []
        d.dispatch({"id": 5, "method": "double", "params": {}}, sent.append)
        assert len(sent) == 1

    def test_malformed_message_ignored(self):
        d = BridgeDispatcher()
        sent = []
        d.dispatch({"method": "x"}, sent.append)      # no id
        d.dispatch("not a dict", sent.append)
        assert sent == []


class TestEvalQueue:
    def test_buffers_until_ready(self):
        q = EvalQueue()
        out = []
        q.send("a()", out.append)
        q.send("b()", out.append)
        assert out == []
        q.mark_ready(out.append)
        assert out == ["a()", "b()"]
        q.send("c()", out.append)
        assert out == ["a()", "b()", "c()"]


class TestFormatting:
    def test_timestamp(self):
        assert format_timestamp(0) == "[00:00:00]"
        assert format_timestamp(75.9) == "[00:01:15]"
        assert format_timestamp(3725) == "[01:02:05]"

    def test_speaker_numbers_by_first_appearance(self):
        segs = [
            Seg("Speaker 2", 0.0, 4.0, "hello"),
            Seg("Speaker 1", 5.0, 8.0, "hi"),
            Seg("Speaker 2", 9.0, 12.0, "again"),
        ]
        lines = segments_to_lines(segs)
        assert [l["speakerNumber"] for l in lines] == [1, 2, 1]
        assert lines[0] == {
            "time": "[00:00:00]",
            "speakerNumber": 1,
            "speakerLabel": "Speaker 2",
            "text": "hello",
        }

    def test_non_numeric_labels_supported(self):
        segs = [Seg("Alice", 0.0, 1.0, "a"), Seg("Bob", 2.0, 3.0, "b")]
        lines = segments_to_lines(segs)
        assert [l["speakerNumber"] for l in lines] == [1, 2]
        assert [l["speakerLabel"] for l in lines] == ["Alice", "Bob"]
