"""FastMCP tool definitions for mem0-lite."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from mem0_lite.access_log import log_tool_call
from mem0_lite.core import (
    StoreBusy,
    clear_session_metrics,
    default_agent_id,
    default_user_id,
    dump,
    feedback_mode,
    last_session_metrics,
    memory_session,
)
from mem0_lite.feedback_log import FEEDBACK_REASONS, log_feedback

FeedbackReason = Literal["used", "empty_ok", "miss", "noise", "stale", "bad_query"]

# FastMCP app registering mem0-lite MCP tools over stdio.
mcp = FastMCP("mem0-lite")


def _store_busy_payload(exc: StoreBusy) -> str:
    """JSON error body when lite.lock wait exceeds MEM0_LITE_LOCK_TIMEOUT."""
    return dump(
        {
            "error": "store_busy",
            "timeout_s": exc.timeout,
            "hint": "Another mem0-lite process is using this MEM0_DIR. Retry.",
        }
    )


def _scope(user_id: str | None, agent_id: str | None) -> tuple[str, str | None]:
    """Resolve tool args to mem0 user_id and optional agent_id."""
    uid = user_id or default_user_id()
    aid = agent_id if agent_id is not None else default_agent_id()
    return uid, aid or None


def _filters(user_id: str | None, agent_id: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build mem0 search/get_all filter dict from scope plus optional metadata keys."""
    uid, aid = _scope(user_id, agent_id)
    filters: dict[str, Any] = {"user_id": uid}
    if aid:
        filters["agent_id"] = aid
    if extra:
        filters.update(extra)
    return filters


def _log_params(**kwargs: Any) -> dict[str, Any]:
    """Read-tool args for access log (omits user_id and unset optional fields)."""
    return {key: value for key, value in kwargs.items() if key != "user_id" and value is not None}


def _attach_ts(body: str, ts: str) -> str:
    """Add ts to a JSON tool response for feedback correlation with access-log."""
    data = json.loads(body)
    if isinstance(data, dict):
        data["ts"] = ts
        return dump(data)
    return dump({"ts": ts, "result": data})


def _run_tool(
    tool: str,
    agent_id: str | None,
    work: Callable[[], str],
    params: dict[str, Any] | None = None,
) -> str:
    """Run a tool handler and append one JSONL line to ~/.mem0/access-log.jsonl."""
    started_at = datetime.now(timezone.utc)
    call_ts = started_at.isoformat()
    t0 = time.perf_counter()
    clear_session_metrics()
    try:
        body = work()
        if feedback_mode():
            return _attach_ts(body, call_ts)
        return body
    finally:
        log_tool_call(
            tool=tool,
            agent_id=agent_id,
            metrics=last_session_metrics(),
            duration_ms=(time.perf_counter() - t0) * 1000,
            timestamp=started_at,
            params=params,
        )


@mcp.tool()
def add_memory(
    text: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    infer: bool = False,
    metadata_json: str | None = None,
) -> str:
    """Store a memory. Prefer infer=false with a self-contained third-person fact.

    memory_type: decision | convention | anti_pattern | user_preference | task_learning | environmental | identity | rule | project
    infer=true lets mem0 extract facts from raw conversation; skip for explicit facts.
    Never store secrets. Skip small talk, tool dumps, and one-shot commands.
    """
    uid, aid = _scope(user_id, agent_id)
    metadata: dict[str, Any] = {}
    if metadata_json:
        parsed = json.loads(metadata_json)
        if not isinstance(parsed, dict):
            raise ValueError("metadata_json must be a JSON object")
        metadata = parsed
    if memory_type:
        metadata["type"] = memory_type

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.add(
                    text,
                    user_id=uid,
                    agent_id=aid,
                    metadata=metadata or None,
                    infer=infer,
                )
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("add_memory", aid, work)


@mcp.tool()
def search_memories(
    query: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    top_k: int = 10,
) -> str:
    """Semantic search. Rewrite the query to 3-6 keywords matching stored third-person facts.

    Do not pass the user's raw message. Drop pronouns and question words.
    When useful, run 2-4 parallel searches with memory_type: decision, convention, anti_pattern, or omit for catch-all.
    When MEM0_LITE_FEEDBACK_MODE is enabled, responses include ts; rate useful/miss/noise via rate_memory_call.
    """
    uid, aid = _scope(user_id, agent_id)
    extra = {"type": memory_type} if memory_type else None

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.search(
                    query,
                    filters=_filters(user_id, agent_id, extra),
                    top_k=top_k,
                )
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool(
        "search_memories",
        aid,
        work,
        params=_log_params(query=query, memory_type=memory_type, top_k=top_k),
    )


@mcp.tool()
def get_memory_by_id(memory_id: str) -> str:
    """Fetch one memory by id. When MEM0_LITE_FEEDBACK_MODE is enabled, rate via rate_memory_call(call_ts)."""
    uid, aid = _scope(None, None)

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.get(memory_id)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        if result is None:
            return dump({"error": "not_found", "id": memory_id})
        return dump(result)

    return _run_tool("get_memory_by_id", aid, work, params=_log_params(memory_id=memory_id))


@mcp.tool()
def list_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    top_k: int = 50,
) -> str:
    """List memories in scope. Not a substitute for search_memories.
    When MEM0_LITE_FEEDBACK_MODE is enabled, responses include ts; rate useful/miss/noise via rate_memory_call.
    """
    uid, aid = _scope(user_id, agent_id)
    extra = {"type": memory_type} if memory_type else None

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.get_all(filters=_filters(user_id, agent_id, extra), top_k=top_k)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool(
        "list_memories",
        aid,
        work,
        params=_log_params(memory_type=memory_type, top_k=top_k),
    )


@mcp.tool()
def update_memory(memory_id: str, text: str) -> str:
    """Replace memory text in place. Prefer this over delete+add when a fact changed."""
    uid, aid = _scope(None, None)

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.update(memory_id, text=text)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("update_memory", aid, work)


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete one memory by id."""
    uid, aid = _scope(None, None)

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.delete(memory_id)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("delete_memory", aid, work)


@mcp.tool()
def delete_all_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    confirm: bool = False,
) -> str:
    """Delete all memories in scope. Requires confirm=true."""
    uid, aid = _scope(user_id, agent_id)
    if not confirm:
        return _run_tool(
            "delete_all_memories",
            aid,
            lambda: dump({"error": "confirm_required", "hint": "Pass confirm=true to wipe this scope."}),
        )

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.delete_all(user_id=uid, agent_id=aid)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("delete_all_memories", aid, work)


@mcp.tool()
def rate_memory_call(
    call_ts: str,
    helpful: bool,
    reason: FeedbackReason,
    note: str | None = None,
) -> str:
    """Rate a prior retrieval by call_ts (from response ts when MEM0_LITE_FEEDBACK_MODE is enabled).

    Call when you used a hit, clearly missed a fact, or got irrelevant noise. Skip empty-and-expected results.
    reason: used | empty_ok | miss | noise | stale | bad_query
    """
    if reason not in FEEDBACK_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(sorted(FEEDBACK_REASONS))}")

    _, aid = _scope(None, None)

    def work() -> str:
        log_feedback(
            call_ts=call_ts,
            agent_id=aid,
            helpful=helpful,
            reason=reason,
            note=note,
        )
        return dump({"ok": True})

    return _run_tool(
        "rate_memory_call",
        aid,
        work,
        params=_log_params(call_ts=call_ts, helpful=helpful, reason=reason, note=note),
    )


def run_mcp() -> None:
    """Run the stdio MCP server (JSON-RPC over stdin/stdout)."""
    mcp.run(transport="stdio")
