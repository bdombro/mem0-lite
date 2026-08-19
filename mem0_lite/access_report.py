"""Access/feedback summaries from JSONL. Never opens Qdrant."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mem0_lite.logs import load_jsonl, log_paths

STORE_BUCKET_ORDER = ("0", "1-10", "11-50", "51+")
RETRIEVAL_TOOLS = frozenset({"search_memories", "list_memories", "get_memory_by_id"})


def store_bucket(n: int) -> str:
    """Bucket label for a collection count."""
    if n <= 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    return "51+"


def effective_count(row: dict[str, Any]) -> int | None:
    """Prefer in-scope count, then global store_count. None if the line has neither."""
    for key in ("scope_count", "store_count"):
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value == int(value):
            return int(value)
    return None


def _empty_bucket() -> dict[str, Any]:
    return {"calls": 0, "rated": 0, "helpful": 0, "helpful_pct": None, "by_reason": {}}


def build_store_snapshot(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Last/min/max store_count from access-log lines. No live store."""
    counts = [row["store_count"] for row in entries if isinstance(row.get("store_count"), int)]
    last = counts[-1] if counts else None
    by_bucket: dict[str, int] = {label: 0 for label in STORE_BUCKET_ORDER}
    for n in counts:
        by_bucket[store_bucket(n)] += 1
    return {
        "last_count": last,
        "min": min(counts) if counts else None,
        "max": max(counts) if counts else None,
        "calls_with_count": len(counts),
        "by_bucket": by_bucket,
    }


def build_feedback_by_store_size(
    feedback: list[dict[str, Any]],
    access_by_ts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Feedback reasons and helpful rate sliced by effective count at call time."""
    buckets: dict[str, dict[str, Any]] = {label: _empty_bucket() for label in STORE_BUCKET_ORDER}
    buckets["(unknown)"] = _empty_bucket()

    for row in access_by_ts.values():
        if row.get("tool") not in RETRIEVAL_TOOLS:
            continue
        n = effective_count(row)
        label = store_bucket(n) if n is not None else "(unknown)"
        buckets[label]["calls"] += 1

    for row in feedback:
        call_ts = row.get("call_ts")
        access = access_by_ts.get(call_ts) if isinstance(call_ts, str) else None
        if access is None or access.get("tool") not in RETRIEVAL_TOOLS:
            continue
        n = effective_count(access)
        label = store_bucket(n) if n is not None else "(unknown)"
        bucket = buckets[label]
        bucket["rated"] += 1
        if row.get("helpful") is True:
            bucket["helpful"] += 1
        reason = row.get("reason")
        if isinstance(reason, str) and reason:
            reasons: dict[str, int] = bucket["by_reason"]
            reasons[reason] = reasons.get(reason, 0) + 1

    out: dict[str, Any] = {}
    for label, bucket in buckets.items():
        if bucket["calls"] == 0 and bucket["rated"] == 0:
            continue
        rated = bucket["rated"]
        bucket["helpful_pct"] = round(100 * bucket["helpful"] / rated, 1) if rated else None
        bucket["by_reason"] = dict(sorted(bucket["by_reason"].items(), key=lambda item: (-item[1], item[0])))
        out[label] = bucket
    return out


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


def build_access_report(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(row["duration_ms"]) for row in entries if "duration_ms" in row]
    lock_waits = [float(row["lock_wait_ms"]) for row in entries if "lock_wait_ms" in row]
    lock_contended = [v for v in lock_waits if v > 0]

    parsed_ts = []
    for row in entries:
        raw = row.get("ts")
        if not isinstance(raw, str):
            continue
        try:
            parsed_ts.append(_parse_ts(raw))
        except ValueError:
            continue

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
        "store": build_store_snapshot(entries),
    }


def build_feedback_report(
    path: Path,
    feedback: list[dict[str, Any]],
    access_by_ts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    helpful_rows = [row for row in feedback if row.get("helpful") is True]
    matched = [row for row in feedback if row.get("call_ts") in access_by_ts]
    unmatched = len(feedback) - len(matched)

    retrieval_calls = [row for row in access_by_ts.values() if row.get("tool") in RETRIEVAL_TOOLS]
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
        "by_store_size": build_feedback_by_store_size(feedback, access_by_ts),
    }


def build_report() -> dict[str, Any]:
    """Summarize access-log.jsonl and feedback.jsonl under MEM0_DIR."""
    paths = log_paths()
    access_path = paths["access"]
    feedback_path = paths["feedback"]
    access_entries = load_jsonl(access_path)
    feedback_entries = load_jsonl(feedback_path)
    access_by_ts = {
        row["ts"]: row for row in access_entries if isinstance(row.get("ts"), str)
    }
    return {
        "access": build_access_report(access_path, access_entries),
        "feedback": build_feedback_report(feedback_path, feedback_entries, access_by_ts),
    }
