"""Pure-Python half of the JS bridge for WKWebView-hosted windows.

No AppKit/WebKit imports here — this module carries the logic that unit
tests exercise (message dispatch, pre-load eval queueing, transcript
formatting). The ObjC glue lives in webwindow.py.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def _js_call(func: str, call_id: int, payload: Any) -> str:
    return (
        "window.speakeasyBridge && window.speakeasyBridge."
        f"{func}({call_id}, {json.dumps(payload)})"
    )


class BridgeDispatcher:
    """Routes {id, method, params} messages to registered handlers.

    Handlers receive (params, respond); respond(result=None, error=None)
    must be called exactly once — synchronously or later (e.g. from a
    save-panel completion handler). Extra calls are ignored.
    """

    def __init__(self) -> None:
        self._methods: dict[str, Callable[[dict, Callable], None]] = {}

    def register(self, method: str, handler: Callable[[dict, Callable], None]) -> None:
        self._methods[method] = handler

    def dispatch(self, message: Any, send_js: Callable[[str], None]) -> None:
        if not isinstance(message, dict):
            return
        call_id = message.get("id")
        method = message.get("method")
        if not isinstance(call_id, int) or not isinstance(method, str):
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}

        answered = False

        def respond(result: Any = None, error: str | None = None) -> None:
            nonlocal answered
            if answered:
                return
            answered = True
            if error is not None:
                send_js(_js_call("_reject", call_id, str(error)))
            else:
                send_js(_js_call("_resolve", call_id, result))

        handler = self._methods.get(method)
        if handler is None:
            respond(error=f"unknown method: {method}")
            return
        try:
            handler(params, respond)
        except Exception as exc:  # surface handler bugs to the page, don't crash the app
            respond(error=f"{type(exc).__name__}: {exc}")


class EvalQueue:
    """Buffers evaluateJavaScript payloads until the page has loaded.

    evaluateJavaScript before didFinishNavigation is silently dropped by
    WebKit — early engine-state pushes would vanish without this.
    """

    def __init__(self) -> None:
        self._ready = False
        self._pending: list[str] = []

    def send(self, js: str, flush: Callable[[str], None]) -> None:
        if self._ready:
            flush(js)
        else:
            self._pending.append(js)

    def mark_ready(self, flush: Callable[[str], None]) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for js in pending:
            flush(js)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"[{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}]"


def segments_to_lines(segments) -> list[dict]:
    """MeetingSegment(speaker, start, end, text) -> page TranscriptLine dicts.

    Speaker numbers are 1-based in order of first appearance, keyed on the
    label — robust to any label format the diarizer produces.
    """
    order: dict[str, int] = {}
    lines: list[dict] = []
    for seg in segments:
        number = order.setdefault(seg.speaker, len(order) + 1)
        lines.append(
            {
                "time": format_timestamp(seg.start),
                "speakerNumber": number,
                "speakerLabel": seg.speaker,
                "text": seg.text,
            }
        )
    return lines
