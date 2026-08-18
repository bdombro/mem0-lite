"""FastMCP tool definitions for mem0-lite."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mem0_lite.core import (
    StoreBusy,
    default_agent_id,
    default_user_id,
    dump,
    memory_session,
)

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
    """
    extra = {"type": memory_type} if memory_type else None
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


@mcp.tool()
def get_memory_by_id(memory_id: str) -> str:
    """Fetch one memory by id."""
    try:
        with memory_session() as memory:
            result = memory.get(memory_id)
    except StoreBusy as exc:
        return _store_busy_payload(exc)
    if result is None:
        return dump({"error": "not_found", "id": memory_id})
    return dump(result)


@mcp.tool()
def list_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    top_k: int = 50,
) -> str:
    """List memories in scope. Not a substitute for search_memories."""
    extra = {"type": memory_type} if memory_type else None
    try:
        with memory_session() as memory:
            result = memory.get_all(filters=_filters(user_id, agent_id, extra), top_k=top_k)
    except StoreBusy as exc:
        return _store_busy_payload(exc)
    return dump(result)


@mcp.tool()
def update_memory(memory_id: str, text: str) -> str:
    """Replace memory text in place. Prefer this over delete+add when a fact changed."""
    try:
        with memory_session() as memory:
            result = memory.update(memory_id, text=text)
    except StoreBusy as exc:
        return _store_busy_payload(exc)
    return dump(result)


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete one memory by id."""
    try:
        with memory_session() as memory:
            result = memory.delete(memory_id)
    except StoreBusy as exc:
        return _store_busy_payload(exc)
    return dump(result)


@mcp.tool()
def delete_all_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    confirm: bool = False,
) -> str:
    """Delete all memories in scope. Requires confirm=true."""
    if not confirm:
        return dump({"error": "confirm_required", "hint": "Pass confirm=true to wipe this scope."})
    uid, aid = _scope(user_id, agent_id)
    try:
        with memory_session() as memory:
            result = memory.delete_all(user_id=uid, agent_id=aid)
    except StoreBusy as exc:
        return _store_busy_payload(exc)
    return dump(result)


def run_mcp() -> None:
    """Run the stdio MCP server (JSON-RPC over stdin/stdout)."""
    mcp.run(transport="stdio")
