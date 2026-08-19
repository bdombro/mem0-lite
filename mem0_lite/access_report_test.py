"""Store counts on access-log lines and report buckets. No live Qdrant."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import mem0_lite.core as core
from mem0_lite.access_log import log_tool_call
from mem0_lite.access_report import (
    build_feedback_by_store_size,
    build_store_snapshot,
    effective_count,
    store_bucket,
)
from mem0_lite.core import (
    SessionMetrics,
    _collection_count,
    _filtered_count,
    _reset_holder,
    last_session_metrics,
    memory_session,
    record_scope_count,
)


class _EmptyMemory:
    def close(self) -> None:
        pass


def test_store_bucket_edges() -> None:
    assert store_bucket(0) == "0"
    assert store_bucket(1) == "1-10"
    assert store_bucket(10) == "1-10"
    assert store_bucket(11) == "11-50"
    assert store_bucket(50) == "11-50"
    assert store_bucket(51) == "51+"


def test_effective_count_prefers_scope() -> None:
    assert effective_count({"scope_count": 2, "store_count": 99}) == 2
    assert effective_count({"store_count": 99}) == 99
    assert effective_count({}) is None


def test_collection_count_from_col_info() -> None:
    class Info:
        points_count = 7

    class Store:
        def col_info(self) -> Info:
            return Info()

    class Memory:
        vector_store = Store()

    assert _collection_count(Memory()) == 7
    assert _collection_count(object()) is None


def test_filtered_count_from_qdrant_client() -> None:
    class Result:
        count = 3

    class Client:
        def count(self, **kwargs: object) -> Result:
            return Result()

    class Store:
        client = Client()
        collection_name = "memories"

        def _create_filter(self, filters: dict) -> dict:
            return filters

    class Memory:
        vector_store = Store()

    assert _filtered_count(Memory(), {"user_id": "u"}) == 3


def test_fake_session_omits_counts(mem0_home, monkeypatch) -> None:
    monkeypatch.setattr(core, "_open_memory", lambda: _EmptyMemory())
    _reset_holder()
    try:
        with memory_session():
            pass
        metrics = last_session_metrics()
        assert metrics is not None
        assert metrics.store_count is None
        assert metrics.scope_count is None
    finally:
        _reset_holder()


def test_record_scope_count_during_session(mem0_home, monkeypatch) -> None:
    class Result:
        count = 4

    class Client:
        def count(self, **kwargs: object) -> Result:
            return Result()

    class Store:
        client = Client()
        collection_name = "memories"

        def col_info(self) -> object:
            return type("Info", (), {"points_count": 12})()

        def _create_filter(self, filters: dict) -> dict:
            return filters

    monkeypatch.setattr(core, "_open_memory", lambda: _EmptyMemory())
    _reset_holder()
    try:
        with memory_session() as memory:
            memory.vector_store = Store()  # type: ignore[attr-defined]
            record_scope_count(memory, {"user_id": "u"})
        metrics = last_session_metrics()
        assert metrics is not None
        assert metrics.scope_count == 4
        assert metrics.store_count == 12
    finally:
        _reset_holder()


def test_access_log_includes_counts(mem0_home) -> None:
    metrics = SessionMetrics(store_count=12, scope_count=3)
    log_tool_call(
        tool="search_memories",
        agent_id=None,
        metrics=metrics,
        duration_ms=1.0,
        timestamp=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    line = (mem0_home / "access-log.jsonl").read_text(encoding="utf-8").strip()
    row = json.loads(line)
    assert row["store_count"] == 12
    assert row["scope_count"] == 3


def test_report_snapshot_and_feedback_buckets() -> None:
    access = [
        {"ts": "t0", "tool": "search_memories", "store_count": 0, "scope_count": 0},
        {"ts": "t1", "tool": "search_memories", "store_count": 8, "scope_count": 2},
        {"ts": "t2", "tool": "add_memory", "store_count": 9},
        {"ts": "t3", "tool": "search_memories", "store_count": 60},
    ]
    snap = build_store_snapshot(access)
    assert snap["last_count"] == 60
    assert snap["min"] == 0
    assert snap["max"] == 60
    assert snap["by_bucket"]["0"] == 1
    assert snap["by_bucket"]["1-10"] == 2
    assert snap["by_bucket"]["51+"] == 1

    feedback = [
        {"call_ts": "t0", "helpful": False, "reason": "empty_ok"},
        {"call_ts": "t1", "helpful": True, "reason": "used"},
        {"call_ts": "t3", "helpful": False, "reason": "noise"},
    ]
    by_size = build_feedback_by_store_size(feedback, {row["ts"]: row for row in access})
    assert by_size["0"]["by_reason"] == {"empty_ok": 1}
    assert by_size["0"]["helpful_pct"] == 0.0
    assert by_size["1-10"]["helpful_pct"] == 100.0
    assert by_size["51+"]["by_reason"] == {"noise": 1}
    assert "11-50" not in by_size
