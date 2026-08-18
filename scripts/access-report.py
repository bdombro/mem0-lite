#!/usr/bin/env python3
"""Summarize ~/.mem0/access-log.jsonl and feedback.jsonl as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mem0_lite.access_log import access_log_path
from mem0_lite.feedback_log import feedback_log_path

RETRIEVAL_TOOLS = frozenset({"search_memories", "list_memories", "get_memory_by_id"})


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * p))
    return ordered[idx]


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None, "p50": None, "p95": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "p50": round(_percentile(values, 0.5) or 0.0, 3),
        "p95": round(_percentile(values, 0.95) or 0.0, 3),
    }


def _feedback_latency_stats(values: list[float]) -> dict[str, float | None]:
    """Min, max, mean, p90 of feedback ts minus call_ts in milliseconds."""
    if not values:
        return {"count": 0, "min_ms": None, "max_ms": None, "mean_ms": None, "p90_ms": None}
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(sum(values) / len(values), 3),
        "p90_ms": round(_percentile(values, 0.9) or 0.0, 3),
    }


def _feedback_delay_ms(row: dict[str, Any]) -> float | None:
    """Milliseconds from call_ts to feedback ts, or None if timestamps are missing/invalid."""
    ts = row.get("ts")
    call_ts = row.get("call_ts")
    if not isinstance(ts, str) or not isinstance(call_ts, str):
        return None
    try:
        return (_parse_ts(ts) - _parse_ts(call_ts)).total_seconds() * 1000
    except ValueError:
        return None


def _feedback_delays_ms(rows: list[dict[str, Any]]) -> list[float]:
    delays: list[float] = []
    for row in rows:
        delay = _feedback_delay_ms(row)
        if delay is not None:
            delays.append(delay)
    return delays


def _count_table(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value = row.get(key)
        label = str(value) if value is not None else "(none)"
        counts[label] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            entries.append(row)
    return entries


def build_access_report(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(row["duration_ms"]) for row in entries if "duration_ms" in row]
    lock_waits = [float(row["lock_wait_ms"]) for row in entries if "lock_wait_ms" in row]
    lock_contended = [v for v in lock_waits if v > 0]

    parsed_ts = [
        _parse_ts(row["ts"]) for row in entries if isinstance(row.get("ts"), str)
    ]

    by_tool: dict[str, dict[str, Any]] = {}
    tool_names = sorted({str(row.get("tool", "(unknown)")) for row in entries})
    for tool in tool_names:
        tool_rows = [row for row in entries if row.get("tool") == tool]
        tool_durations = [float(row["duration_ms"]) for row in tool_rows if "duration_ms" in row]
        tool_locks = [float(row["lock_wait_ms"]) for row in tool_rows if "lock_wait_ms" in row]
        by_tool[tool] = {
            "calls": len(tool_rows),
            "connection": _count_table(tool_rows, "connection"),
            "duration_ms": _stats(tool_durations),
            "lock_wait_ms": _stats(tool_locks),
        }

    return {
        "path": str(path),
        "exists": path.is_file(),
        "total_calls": len(entries),
        "time_range": {
            "first_ts": parsed_ts[0].isoformat() if parsed_ts else None,
            "last_ts": parsed_ts[-1].isoformat() if parsed_ts else None,
        },
        "by_tool": by_tool,
        "by_agent_id": _count_table(entries, "agent_id"),
        "connection": _count_table(entries, "connection"),
        "duration_ms": _stats(durations),
        "lock_wait_ms": _stats(lock_waits),
        "lock_contention": {
            "calls_with_wait": len(lock_contended),
            "stats_ms": _stats(lock_contended),
        },
    }


def build_feedback_report(
    path: Path,
    feedback: list[dict[str, Any]],
    access_by_ts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    helpful_rows = [row for row in feedback if row.get("helpful") is True]
    matched = [row for row in feedback if row.get("call_ts") in access_by_ts]
    unmatched = len(feedback) - len(matched)

    retrieval_calls = [
        row
        for row in access_by_ts.values()
        if row.get("tool") in RETRIEVAL_TOOLS
    ]
    rated_retrieval_ts = {
        row["call_ts"]
        for row in feedback
        if isinstance(row.get("call_ts"), str)
        and row["call_ts"] in access_by_ts
        and access_by_ts[row["call_ts"]].get("tool") in RETRIEVAL_TOOLS
    }

    by_tool: dict[str, dict[str, Any]] = {}
    for tool in sorted(RETRIEVAL_TOOLS):
        tool_calls = [row for row in retrieval_calls if row.get("tool") == tool]
        tool_ts = {row["ts"] for row in tool_calls if isinstance(row.get("ts"), str)}
        tool_feedback = [
            row
            for row in feedback
            if isinstance(row.get("call_ts"), str) and row["call_ts"] in tool_ts
        ]
        rated = len(tool_feedback)
        calls = len(tool_calls)
        by_tool[tool] = {
            "calls": calls,
            "rated": rated,
            "coverage_pct": round(100 * rated / calls, 1) if calls else None,
            "helpful": sum(1 for row in tool_feedback if row.get("helpful") is True),
            "by_reason": _count_table(tool_feedback, "reason"),
            "feedback_latency_ms": _feedback_latency_stats(_feedback_delays_ms(tool_feedback)),
        }

    return {
        "path": str(path),
        "exists": path.is_file(),
        "total_ratings": len(feedback),
        "matched_to_access_log": len(matched),
        "unmatched_call_ts": unmatched,
        "helpful_count": len(helpful_rows),
        "helpful_pct": round(100 * len(helpful_rows) / len(feedback), 1) if feedback else None,
        "by_reason": _count_table(feedback, "reason"),
        "by_agent_id": _count_table(feedback, "agent_id"),
        "feedback_latency_ms": _feedback_latency_stats(_feedback_delays_ms(feedback)),
        "retrieval_coverage": {
            "retrieval_calls": len(retrieval_calls),
            "rated": len(rated_retrieval_ts),
            "coverage_pct": round(
                100 * len(rated_retrieval_ts) / len(retrieval_calls), 1
            )
            if retrieval_calls
            else None,
            "by_tool": by_tool,
        },
    }


def build_report(access_path: Path, feedback_path: Path) -> dict[str, Any]:
    access_entries = load_entries(access_path)
    feedback_entries = load_entries(feedback_path)
    access_by_ts = {
        row["ts"]: row
        for row in access_entries
        if isinstance(row.get("ts"), str)
    }
    return {
        "access": build_access_report(access_path, access_entries),
        "feedback": build_feedback_report(feedback_path, feedback_entries, access_by_ts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize mem0-lite access-log.jsonl and feedback.jsonl as JSON."
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(access_log_path()),
        help="access log path (default: ~/.mem0/access-log.jsonl or MEM0_DIR)",
    )
    parser.add_argument(
        "--feedback-path",
        type=Path,
        default=Path(feedback_log_path()),
        help="feedback log path (default: ~/.mem0/feedback.jsonl or MEM0_DIR)",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(args.path, args.feedback_path)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
