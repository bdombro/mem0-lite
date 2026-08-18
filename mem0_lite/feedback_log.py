"""Append-only JSONL feedback log for memory tool effectiveness."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from mem0_lite.core import mem0_dir

# Serializes append lines to feedback.jsonl.
_log_lock = threading.Lock()

FEEDBACK_REASONS = frozenset(
    {"used", "empty_ok", "miss", "noise", "stale", "bad_query"},
)


def feedback_log_path() -> str:
    """Path to ~/.mem0/feedback.jsonl (or under MEM0_DIR)."""
    return str(mem0_dir() / "feedback.jsonl")


def log_feedback(
    *,
    call_ts: str,
    agent_id: str | None,
    helpful: bool,
    reason: str,
    note: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Append one feedback record linked to an access-log ts."""
    if reason not in FEEDBACK_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(sorted(FEEDBACK_REASONS))}")

    ts = timestamp or datetime.now(timezone.utc)
    entry: dict[str, Any] = {
        "ts": ts.isoformat(),
        "call_ts": call_ts,
        "agent_id": agent_id,
        "helpful": helpful,
        "reason": reason,
    }
    if note:
        entry["note"] = note

    line = json.dumps(entry, separators=(",", ":"))
    path = feedback_log_path()
    with _log_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
