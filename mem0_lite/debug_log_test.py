"""Opt-in debug.jsonl: request/response clip, off by default."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from mem0_lite.debug_log import (
    MAX_LIST_ITEMS,
    MAX_STRING_CHARS,
    _clip,
    debug_log_path,
    log_debug_call,
)
from mem0_lite.mcp import add_memory, search_memories, update_memory


def _read_debug(home) -> list[dict[str, Any]]:
    path = home / "debug.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_debug_off_writes_nothing(mem0_home) -> None:
    log_debug_call(
        ts="2026-08-18T00:00:00+00:00",
        tool="search_memories",
        agent_id=None,
        request={"query": "pnpm"},
        response={"results": []},
    )
    assert not (mem0_home / "debug.jsonl").exists()


def test_debug_on_writes_request_and_parsed_response(mem0_home, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_DEBUG", "1")
    log_debug_call(
        ts="2026-08-18T00:00:00+00:00",
        tool="search_memories",
        agent_id="cursor",
        request={"query": "pnpm", "filters": {"project": "mem0-lite"}},
        response='{\n  "results": []\n}',
        duration_ms=12.3456,
    )
    rows = _read_debug(mem0_home)
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == "2026-08-18T00:00:00+00:00"
    assert row["tool"] == "search_memories"
    assert row["agent_id"] == "cursor"
    assert row["request"]["filters"] == {"project": "mem0-lite"}
    assert row["response"] == {"results": []}
    assert row["duration_ms"] == 12.346
    assert str(debug_log_path()) == str(mem0_home / "debug.jsonl")


def test_line_cap_replaces_response(mem0_home, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_DEBUG", "1")
    monkeypatch.setattr("mem0_lite.debug_log.MAX_LINE_CHARS", 200)
    log_debug_call(
        ts="2026-08-18T00:00:00+00:00",
        tool="search_memories",
        agent_id=None,
        request={"query": "q"},
        response={"results": [{"memory": "m" * 400}]},
    )
    row = _read_debug(mem0_home)[0]
    assert row["response"]["_truncated"] is True
    assert row["response"]["preview"].endswith("...truncated")


def test_clip_long_string_and_list() -> None:
    clipped = _clip("x" * (MAX_STRING_CHARS + 10))
    assert isinstance(clipped, str)
    assert clipped.endswith("...truncated")
    assert clipped.startswith("x" * MAX_STRING_CHARS)

    items = _clip([{"memory": "a"}] * (MAX_LIST_ITEMS + 5))
    assert isinstance(items, list)
    assert items[-1] == {"_omitted": 5}
    assert len(items) == MAX_LIST_ITEMS + 1


class _FakeMemory:
    def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": [{"id": "m1"}]}

    def search(self, query: str, filters: Any = None, top_k: int = 10) -> dict[str, Any]:
        self.filters = filters
        return {"results": [{"memory": "User prefers pnpm"}]}

    def get_all(self, filters: Any = None, top_k: int = 50) -> dict[str, Any]:
        return {"results": []}


@contextmanager
def _fake_session() -> Iterator[_FakeMemory]:
    yield _FakeMemory()


def test_search_debug_includes_effective_filters(mem0_home, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_DEBUG", "1")
    monkeypatch.setattr("mem0_lite.mcp.memory_session", _fake_session)
    body = search_memories(query="pnpm typescript", memory_type="decision", project="github.com/bdombro/mem0-lite")
    assert "pnpm" in body
    rows = _read_debug(mem0_home)
    assert len(rows) == 1
    request = rows[0]["request"]
    assert request["query"] == "pnpm typescript"
    assert request["filters"]["type"] == "decision"
    assert request["filters"]["project"] == "github.com/bdombro/mem0-lite"
    assert "user_id" in request["filters"]
    assert rows[0]["response"]["results"][0]["memory"] == "User prefers pnpm"


def test_add_debug_includes_text_and_tags(mem0_home, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_DEBUG", "1")
    monkeypatch.setattr("mem0_lite.mcp.memory_session", _fake_session)
    add_memory(
        text="User prefers pnpm in TypeScript packages",
        memory_type="convention",
        project="github.com/bdombro/mem0-lite",
        scope="standing",
    )
    rows = _read_debug(mem0_home)
    assert len(rows) == 1
    request = rows[0]["request"]
    assert request["text"].startswith("User prefers pnpm")
    assert request["tags"]["project"] == "github.com/bdombro/mem0-lite"
    assert request["tags"]["scope"] == "standing"
    assert rows[0]["response"]["results"][0]["id"] == "m1"


def test_update_error_still_logged(mem0_home, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_DEBUG", "1")
    body = update_memory(memory_id="missing")
    assert "text_or_metadata_required" in body
    rows = _read_debug(mem0_home)
    assert rows[0]["tool"] == "update_memory"
    assert rows[0]["request"]["memory_id"] == "missing"
    assert rows[0]["response"]["error"] == "text_or_metadata_required"


def test_debug_off_mcp_skips_file(mem0_home, monkeypatch) -> None:
    monkeypatch.setattr("mem0_lite.mcp.memory_session", _fake_session)
    search_memories(query="pnpm")
    assert not (mem0_home / "debug.jsonl").exists()
    assert (mem0_home / "access-log.jsonl").is_file()
