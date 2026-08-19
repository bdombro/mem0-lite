"""JSONL log queries: getByTs, getColumn, load."""

from __future__ import annotations

import json

from mem0_lite.cli import main
from mem0_lite.logs import format_cell, get_by_ts, get_column, lookup_column


def _write_jsonl(home, name: str, rows: list[dict]) -> None:
    path = home / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_lookup_column_dotted() -> None:
    row = {"params": {"query": "pnpm"}, "tool": "search_memories"}
    assert lookup_column(row, "tool") == "search_memories"
    assert lookup_column(row, "params.query") == "pnpm"
    from mem0_lite.logs import _MISSING

    assert lookup_column(row, "params.missing") is _MISSING


def test_format_cell_quotes_commas() -> None:
    assert format_cell(True) == "true"
    assert format_cell({"a": 1}) == '{"a":1}'
    assert format_cell("a,b") == '"a,b"'


def test_get_by_ts_joins_feedback_and_debug(mem0_home) -> None:
    call_ts = "2026-08-18T14:00:00+00:00"
    rate_ts = "2026-08-18T14:00:05+00:00"
    _write_jsonl(
        mem0_home,
        "access-log.jsonl",
        [{"ts": call_ts, "tool": "search_memories", "store_count": 3}],
    )
    _write_jsonl(
        mem0_home,
        "debug.jsonl",
        [{"ts": call_ts, "tool": "search_memories", "request": {"query": "pnpm"}}],
    )
    _write_jsonl(
        mem0_home,
        "feedback.jsonl",
        [{"ts": rate_ts, "call_ts": call_ts, "helpful": True, "reason": "used"}],
    )

    by_call = get_by_ts(call_ts)
    assert by_call["access"][0]["tool"] == "search_memories"
    assert by_call["debug"][0]["request"]["query"] == "pnpm"
    assert by_call["feedback"][0]["reason"] == "used"

    by_rate = get_by_ts(rate_ts)
    assert by_rate["access"][0]["ts"] == call_ts
    assert by_rate["feedback"][0]["ts"] == rate_ts


def test_get_column_across_logs(mem0_home) -> None:
    _write_jsonl(
        mem0_home,
        "access-log.jsonl",
        [
            {"ts": "t0", "tool": "search_memories", "params": {"query": "a"}},
            {"ts": "t1", "tool": "add_memory"},
        ],
    )
    _write_jsonl(
        mem0_home,
        "feedback.jsonl",
        [{"ts": "t2", "call_ts": "t0", "reason": "used"}],
    )
    _write_jsonl(
        mem0_home,
        "debug.jsonl",
        [{"ts": "t0", "tool": "search_memories", "request": {"query": "a"}}],
    )
    assert get_column("tool") == "search_memories,add_memory,search_memories"
    assert get_column("reason") == "used"
    assert get_column("params.query") == "a"
    assert get_column("request.query") == "a"
    assert get_column("nope") == ""


def test_cli_log_report_and_queries(mem0_home, capsys) -> None:
    _write_jsonl(
        mem0_home,
        "access-log.jsonl",
        [{"ts": "t0", "tool": "search_memories", "duration_ms": 1.0, "lock_wait_ms": 0.0}],
    )
    assert main(["log", "report"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["access"]["total_calls"] == 1

    assert main(["log", "getByTs", "t0"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["access"][0]["tool"] == "search_memories"

    assert main(["log", "getColumn", "tool"]) == 0
    assert capsys.readouterr().out.strip() == "search_memories"
