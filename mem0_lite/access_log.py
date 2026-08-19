"""Append-only JSONL access log for MCP tool calls."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from mem0_lite.core import SessionMetrics, mem0_dir

# Serializes append lines to access-log.jsonl.
_log_lock = threading.Lock()


def access_log_path() -> str:
    """Path to ~/.mem0/access-log.jsonl (or under MEM0_DIR)."""
    return str(mem0_dir() / "access-log.jsonl")


def log_tool_call(
    *,
    tool: str,
    agent_id: str | None,
    metrics: SessionMetrics | None,
    duration_ms: float,
    timestamp: datetime,
    params: dict[str, Any] | None = None,
) -> None:
    """Append one tool-call record as JSONL."""
    if metrics is None:
        connection = "none"
        lock_wait_ms = 0.0
    elif metrics.reused_connection:
        connection = "reuse"
        lock_wait_ms = metrics.lock_wait_ms
    else:
        connection = "open"
        lock_wait_ms = metrics.lock_wait_ms

    entry: dict[str, Any] = {
        "ts": timestamp.isoformat(),
        "tool": tool,
        "agent_id": agent_id,
        "connection": connection,
        "lock_wait_ms": round(lock_wait_ms, 3),
        "duration_ms": round(duration_ms, 3),
    }
    if params:
        entry["params"] = params
    if metrics is not None:
        if metrics.store_count is not None:
            entry["store_count"] = metrics.store_count
        if metrics.scope_count is not None:
            entry["scope_count"] = metrics.scope_count

    line = json.dumps(entry, separators=(",", ":"))
    path = access_log_path()
    with _log_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
