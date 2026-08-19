"""Append-only JSONL debug log for MCP tool calls (opt-in, not the access log)."""

from __future__ import annotations

import json
import threading
from typing import Any

from mem0_lite.core import debug_mode, mem0_dir

# Serializes append lines to debug.jsonl.
_log_lock = threading.Lock()

# Per-string clip in request/response payloads.
MAX_STRING_CHARS = 2048
# Search/list hits kept before an omitted-count marker.
MAX_LIST_ITEMS = 20
# Whole JSONL line cap; response is dropped to a preview if exceeded.
MAX_LINE_CHARS = 32768


def debug_log_path() -> str:
    """Path to ~/.mem0/debug.jsonl (or under MEM0_DIR)."""
    return str(mem0_dir() / "debug.jsonl")


def _clip(value: Any, *, max_string: int = MAX_STRING_CHARS) -> Any:
    """Truncate long strings and long lists. Dicts are clipped recursively."""
    if isinstance(value, str):
        if len(value) <= max_string:
            return value
        return value[:max_string] + "...truncated"
    if isinstance(value, dict):
        return {key: _clip(item, max_string=max_string) for key, item in value.items()}
    if isinstance(value, list):
        items = [_clip(item, max_string=max_string) for item in value[:MAX_LIST_ITEMS]]
        omitted = len(value) - MAX_LIST_ITEMS
        if omitted > 0:
            items.append({"_omitted": omitted})
        return items
    return value


def _parse_response(response: Any) -> Any:
    """Prefer a JSON object; keep a raw string if the body is not JSON."""
    if response is None:
        return None
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return response
    return response


def _dumps(entry: dict[str, Any]) -> str:
    return json.dumps(entry, separators=(",", ":"), default=str)


def log_debug_call(
    *,
    ts: str,
    tool: str,
    agent_id: str | None,
    request: dict[str, Any] | None,
    response: Any,
    duration_ms: float | None = None,
) -> None:
    """Append one debug record when MEM0_LITE_DEBUG is on. No-op otherwise.

    request should be the effective call (agent args plus tags/filters).
    response is the tool body (JSON string or object). Never writes to stdio.
    """
    if not debug_mode():
        return

    entry: dict[str, Any] = {
        "ts": ts,
        "tool": tool,
        "agent_id": agent_id,
        "request": _clip(request or {}),
        "response": _clip(_parse_response(response)),
    }
    if duration_ms is not None:
        entry["duration_ms"] = round(duration_ms, 3)

    line = _dumps(entry)
    if len(line) > MAX_LINE_CHARS:
        preview = _dumps(entry.get("response"))[:MAX_STRING_CHARS]
        entry["response"] = {"_truncated": True, "preview": preview + "...truncated"}
        line = _dumps(entry)

    path = debug_log_path()
    with _log_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
