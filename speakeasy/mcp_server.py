"""Local MCP server: JSON-RPC 2.0 over stdio for Claude Desktop and Code.

The user's Claude client starts `Speakeasy --mcp` and talks to it over
pipes; Speakeasy itself still makes no network calls. Dispatched before
argparse (see __main__), so no model, AppKit, microphone or Dock icon.

Stdout carries only protocol messages. main() keeps a private duplicate of
fd 1 for JSON-RPC, then points fd 1 and sys.stdout at stderr before any
tool runs, so a stray print() — or a C library writing to fd 1 — can never
corrupt the stream the client parses.
"""

import json
import os
import sqlite3
import sys
from typing import BinaryIO, Callable

from .meeting_library import MeetingLibrary, MeetingNotFound
from .mcp_tools import ToolError, build_tools, record_use

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-06-18"
SERVER_VERSION = "1.0.0"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

INSTRUCTIONS = (
    "Speakeasy's local meeting library (transcripts recorded on this Mac). "
    "Start with search_meetings or list_meetings, then read only what you need "
    "with get_transcript (start_seconds/end_seconds, or cursor). Dates are the "
    "user's local days, YYYY-MM-DD. save_notes stores a summary, action items "
    "or tags the user can see in Speakeasy."
)
_BUSY = "The meeting library is busy. Try again in a moment."


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _valid_id(rid) -> bool:
    return isinstance(rid, str) or (isinstance(rid, int) and not isinstance(rid, bool))


class Server:
    def __init__(self, tools: dict, on_call: Callable[[], None] | None = None) -> None:
        self.tools = tools
        self.on_call = on_call

    def handle_line(self, line: bytes) -> dict | None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _error(None, PARSE_ERROR, "Parse error")
        return self.handle(message)

    def handle(self, message) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Invalid Request")
        if "method" not in message and ("result" in message or "error" in message):
            return None  # a client's response; this server sends no requests
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            rid = message.get("id")
            return _error(rid if _valid_id(rid) else None, INVALID_REQUEST, "Invalid Request")
        if "id" not in message:
            return None  # notification: never answered
        rid = message["id"]
        if not _valid_id(rid):
            return _error(None, INVALID_REQUEST, "Invalid Request")
        method, params = message["method"], message.get("params")
        if method == "initialize":
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            return self._result(rid, {
                "protocolVersion": requested if requested in SUPPORTED_VERSIONS else DEFAULT_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "speakeasy", "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return self._result(rid, {})
        if method == "tools/list":
            return self._result(rid, {"tools": [t.definition() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(rid, params)
        return _error(rid, METHOD_NOT_FOUND, f"Method not found: {method}")

    @staticmethod
    def _result(rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _call(self, rid, params):
        if not isinstance(params, dict) or params.get("name") not in self.tools:
            name = params.get("name") if isinstance(params, dict) else None
            return _error(rid, INVALID_PARAMS, f"Unknown tool: {name}")
        args = params.get("arguments", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(rid, INVALID_PARAMS, "arguments must be an object")
        if self.on_call is not None:
            try:
                self.on_call()
            except Exception:
                pass  # "last used" is cosmetic; never fail the call over it
        tool = self.tools[params["name"]]
        try:
            text, is_error = json.dumps(tool.run(args), ensure_ascii=False), False
        except ToolError as err:
            text, is_error = str(err), True
        except MeetingNotFound as err:
            text, is_error = f"No meeting with id {err.args[0]}.", True
        except ValueError as err:
            text, is_error = str(err), True
        except sqlite3.OperationalError as err:
            busy = any(w in str(err).lower() for w in ("locked", "busy"))
            if not busy:
                print(f"mcp: {tool.name} failed: {type(err).__name__}", file=sys.stderr)
            text, is_error = (_BUSY if busy else "Speakeasy hit an internal error."), True
        except Exception as err:
            # Type only: exception messages can carry paths or meeting text.
            print(f"mcp: {tool.name} failed: {type(err).__name__}", file=sys.stderr)
            text, is_error = "Speakeasy hit an internal error.", True
        return self._result(rid, {"content": [{"type": "text", "text": text}],
                                  "isError": is_error})


def serve(infile: BinaryIO, outfile: BinaryIO, server: Server) -> None:
    for line in iter(infile.readline, b""):
        if not line.strip():
            continue
        reply = server.handle_line(line.strip())
        if reply is None:
            continue
        try:
            outfile.write(json.dumps(reply, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8") + b"\n")
            outfile.flush()
        except BrokenPipeError:
            return


def main() -> None:
    protocol_out = os.fdopen(os.dup(1), "wb")
    protocol_in = os.fdopen(os.dup(0), "rb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    server = Server(build_tools(MeetingLibrary()), on_call=record_use)
    try:
        serve(protocol_in, protocol_out, server)
    except KeyboardInterrupt:
        pass
