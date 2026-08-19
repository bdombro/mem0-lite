"""Read-only JSONL log queries. Never opens Qdrant."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mem0_lite.access_log import access_log_path
from mem0_lite.debug_log import debug_log_path
from mem0_lite.feedback_log import feedback_log_path

_MISSING = object()

LOG_NAMES = ("access", "feedback", "debug")


def log_paths() -> dict[str, Path]:
    """Named log files under MEM0_DIR."""
    return {
        "access": Path(access_log_path()),
        "feedback": Path(feedback_log_path()),
        "debug": Path(debug_log_path()),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file of objects. Missing file → empty list."""
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


def load_all_logs() -> dict[str, list[dict[str, Any]]]:
    """Load access, feedback, and debug logs."""
    return {name: load_jsonl(path) for name, path in log_paths().items()}


def _row_timestamps(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("ts", "call_ts"):
        value = row.get(field)
        if isinstance(value, str) and value:
            keys.add(value)
    return keys


def _correlate_keys(logs: dict[str, list[dict[str, Any]]], ts: str) -> set[str]:
    """Expand ts through ts/call_ts links so a rating ts still finds the tool call."""
    keys = {ts}
    changed = True
    while changed:
        changed = False
        for rows in logs.values():
            for row in rows:
                row_keys = _row_timestamps(row)
                if row_keys & keys and row_keys - keys:
                    keys |= row_keys
                    changed = True
    return keys


def get_by_ts(ts: str) -> dict[str, Any]:
    """Aggregate log rows that share ts or call_ts (including via feedback links)."""
    logs = load_all_logs()
    keys = _correlate_keys(logs, ts)
    matched: dict[str, list[dict[str, Any]]] = {}
    for name in LOG_NAMES:
        matched[name] = [row for row in logs[name] if _row_timestamps(row) & keys]
    return {"ts": ts, **matched}


def lookup_column(row: dict[str, Any], column: str) -> Any:
    """Dotted path into a JSON object. Returns _MISSING if any segment is absent."""
    current: Any = row
    for part in column.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def format_cell(value: Any) -> str:
    """CSV cell: scalars as text; objects as compact JSON; comma/quote → JSON string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    text = str(value)
    if "," in text or '"' in text or "\n" in text:
        return json.dumps(text)
    return text


def get_column(column: str) -> str:
    """Comma-separated values of one column (dotted path) across all logs."""
    cells: list[str] = []
    for name in LOG_NAMES:
        for row in load_jsonl(log_paths()[name]):
            value = lookup_column(row, column)
            if value is _MISSING:
                continue
            cells.append(format_cell(value))
    return ",".join(cells)
