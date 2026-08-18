"""stdio MCP server. Memory() is loaded once per Cursor MCP session."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mem0_lite_mcp.memory import default_agent_id, default_user_id, get_memory
from mem0_lite_mcp.serialize import dump

mcp = FastMCP("mem0-lite")


def _scope(user_id: str | None, agent_id: str | None) -> tuple[str, str | None]:
    uid = user_id or default_user_id()
    aid = agent_id if agent_id is not None else default_agent_id()
    return uid, aid or None


def _filters(user_id: str | None, agent_id: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
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
    result = get_memory().add(
        text,
        user_id=uid,
        agent_id=aid,
        metadata=metadata or None,
        infer=infer,
    )
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
    """
    extra = {"type": memory_type} if memory_type else None
    result = get_memory().search(
        query,
        filters=_filters(user_id, agent_id, extra),
        top_k=top_k,
    )
    return dump(result)


@mcp.tool()
def get_memory_by_id(memory_id: str) -> str:
    """Fetch one memory by id."""
    result = get_memory().get(memory_id)
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
    result = get_memory().get_all(filters=_filters(user_id, agent_id, extra), top_k=top_k)
    return dump(result)


@mcp.tool()
def update_memory(memory_id: str, text: str) -> str:
    """Replace memory text in place. Prefer this over delete+add when a fact changed."""
    result = get_memory().update(memory_id, text=text)
    return dump(result)


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete one memory by id."""
    result = get_memory().delete(memory_id)
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
    result = get_memory().delete_all(user_id=uid, agent_id=aid)
    return dump(result)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
