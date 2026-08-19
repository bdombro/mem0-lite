"""FastMCP tool definitions for mem0-lite."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from mem0_lite.access_log import log_tool_call
from mem0_lite.context import envelope, gather_context, kick_reconcile, pick_follow_up, recall_extra
from mem0_lite.core import (
    StoreBusy,
    clear_session_metrics,
    default_agent_id,
    default_user_id,
    dump,
    feedback_mode,
    last_session_metrics,
    memory_session,
    record_scope_count,
)
from mem0_lite.debug_log import log_debug_call
from mem0_lite.feedback_log import FEEDBACK_REASONS, log_feedback
from mem0_lite.scope import normalize_scope

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


def _debug_request(**kwargs: Any) -> dict[str, Any]:
    """Agent args for debug.jsonl (drops unset fields). Handlers may add tags/filters."""
    return {key: value for key, value in kwargs.items() if value is not None}


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
    debug_request: dict[str, Any] | None = None,
) -> str:
    """Run a tool handler; access-log always, debug.jsonl when MEM0_LITE_DEBUG is on."""
    started_at = datetime.now(timezone.utc)
    call_ts = started_at.isoformat()
    t0 = time.perf_counter()
    clear_session_metrics()
    body: str | None = None
    try:
        body = work()
        if feedback_mode():
            return _attach_ts(body, call_ts)
        return body
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_tool_call(
            tool=tool,
            agent_id=agent_id,
            metrics=last_session_metrics(),
            duration_ms=duration_ms,
            timestamp=started_at,
            params=params,
        )
        log_debug_call(
            ts=call_ts,
            tool=tool,
            agent_id=agent_id,
            request=debug_request if debug_request is not None else params,
            response=body,
            duration_ms=duration_ms,
        )


def _parse_metadata_json(metadata_json: str | None) -> dict[str, Any]:
    """Parse metadata_json into a dict; empty if unset."""
    if not metadata_json:
        return {}
    parsed = json.loads(metadata_json)
    if not isinstance(parsed, dict):
        raise ValueError("metadata_json must be a JSON object")
    return parsed


@mcp.tool()
def add_memory(
    text: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    infer: bool = False,
    metadata_json: str | None = None,
    project: str | None = None,
    repo: str | None = None,
    workstream: str | None = None,
    source_ref: str | None = None,
    scope: str | None = None,
    cwd: str | None = None,
) -> str:
    """Store a memory. Prefer infer=false with a self-contained third-person fact.

    memory_type: decision | convention | anti_pattern | user_preference | task_learning | environmental | identity | rule | project
    Optional project (repo is an alias), workstream, source_ref, scope (standing|wip|historical), cwd.
    Explicit tags win. If cwd is set and tags are omitted, plugins may stamp them (bundled git).
    Probe failure: warning, write proceeds untagged or with explicit tags. Do not invent project from a path.
    infer=true lets mem0 extract facts from raw conversation; skip for explicit facts.
    Never store secrets. Skip small talk, tool dumps, and one-shot commands.
    """
    uid, aid = _scope(user_id, agent_id)
    debug_request = _debug_request(
        text=text,
        infer=infer,
        memory_type=memory_type,
        project=project,
        repo=repo,
        workstream=workstream,
        source_ref=source_ref,
        scope=scope,
        cwd=cwd,
        metadata_json=metadata_json,
    )
    access_params = _log_params(
        memory_type=memory_type,
        project=project or repo,
        workstream=workstream,
        scope=scope,
        cwd=cwd,
    )
    if scope and scope.strip() and normalize_scope(scope) is None:
        return _run_tool(
            "add_memory",
            aid,
            lambda: dump({"error": "invalid_scope", "hint": "standing | wip | historical"}),
            params=access_params,
            debug_request=debug_request,
        )

    def work() -> str:
        metadata = _parse_metadata_json(metadata_json)
        if memory_type:
            metadata["type"] = memory_type
        tags, _, _, warning = gather_context(
            tool="add_memory",
            user_id=uid,
            agent_id=aid,
            cwd=cwd,
            project=project,
            repo=repo,
            workstream=workstream,
            source_ref=source_ref,
            scope=scope,
        )
        debug_request["tags"] = tags
        metadata.update(tags)
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
        if warning and isinstance(result, dict):
            result = dict(result)
            result["warning"] = warning
        return dump(result)

    return _run_tool(
        "add_memory",
        aid,
        work,
        params=access_params,
        debug_request=debug_request,
    )


@mcp.tool()
def search_memories(
    query: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    top_k: int = 10,
    project: str | None = None,
    repo: str | None = None,
    workstream: str | None = None,
    scope: str | None = None,
    cwd: str | None = None,
) -> str:
    """Semantic search. Rewrite the query to 3-6 keywords matching stored third-person facts.

    Do not pass the user's raw message. Drop pronouns and question words.
    When useful, run 2-4 parallel searches with memory_type: decision, convention, anti_pattern, environmental, or omit for catch-all.
    Pass cwd when in a checkout, or project/workstream if already known. repo is an alias for project.
    If those filters are omitted and project is known, default recall is standing+project OR current wip workstream.
    When MEM0_LITE_FEEDBACK_MODE is enabled, responses include ts; rate useful/miss/noise via rate_memory_call.
    """
    uid, aid = _scope(user_id, agent_id)
    debug_request = _debug_request(
        query=query,
        memory_type=memory_type,
        top_k=top_k,
        project=project,
        repo=repo,
        workstream=workstream,
        scope=scope,
        cwd=cwd,
    )

    def work() -> str:
        extra, _, pairs, warning = recall_extra(
            user_id=uid,
            agent_id=aid,
            cwd=cwd,
            project=project,
            repo=repo,
            workstream=workstream,
            scope=scope,
            memory_type=memory_type,
        )
        filters = _filters(user_id, agent_id, extra or None)
        debug_request["filters"] = filters
        try:
            with memory_session() as memory:
                result = memory.search(
                    query,
                    filters=filters,
                    top_k=top_k,
                )
                record_scope_count(memory, filters)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        follow_up = pick_follow_up(pairs, result, warning)
        kick_reconcile(pairs, user_id=uid, agent_id=aid)
        return dump(envelope(result, warning=warning, follow_up=follow_up))

    return _run_tool(
        "search_memories",
        aid,
        work,
        params=_log_params(
            query=query,
            memory_type=memory_type,
            top_k=top_k,
            project=project or repo,
            workstream=workstream,
            scope=scope,
            cwd=cwd,
        ),
        debug_request=debug_request,
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

    return _run_tool(
        "get_memory_by_id",
        aid,
        work,
        params=_log_params(memory_id=memory_id),
        debug_request=_debug_request(memory_id=memory_id),
    )


@mcp.tool()
def list_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    memory_type: str | None = None,
    top_k: int = 50,
    project: str | None = None,
    repo: str | None = None,
    workstream: str | None = None,
    scope: str | None = None,
    cwd: str | None = None,
) -> str:
    """List memories in scope. Not a substitute for search_memories.

    Optional project (repo alias), workstream, scope, cwd — same default recall as search_memories.
    When MEM0_LITE_FEEDBACK_MODE is enabled, responses include ts; rate useful/miss/noise via rate_memory_call.
    """
    uid, aid = _scope(user_id, agent_id)
    debug_request = _debug_request(
        memory_type=memory_type,
        top_k=top_k,
        project=project,
        repo=repo,
        workstream=workstream,
        scope=scope,
        cwd=cwd,
    )

    def work() -> str:
        extra, _, pairs, warning = recall_extra(
            user_id=uid,
            agent_id=aid,
            cwd=cwd,
            project=project,
            repo=repo,
            workstream=workstream,
            scope=scope,
            memory_type=memory_type,
        )
        filters = _filters(user_id, agent_id, extra or None)
        debug_request["filters"] = filters
        try:
            with memory_session() as memory:
                result = memory.get_all(filters=filters, top_k=top_k)
                record_scope_count(memory, filters)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        follow_up = pick_follow_up(pairs, result, warning)
        kick_reconcile(pairs, user_id=uid, agent_id=aid)
        return dump(envelope(result, warning=warning, follow_up=follow_up))

    return _run_tool(
        "list_memories",
        aid,
        work,
        params=_log_params(
            memory_type=memory_type,
            top_k=top_k,
            project=project or repo,
            workstream=workstream,
            scope=scope,
            cwd=cwd,
        ),
        debug_request=debug_request,
    )


@mcp.tool()
def update_memory(
    memory_id: str,
    text: str | None = None,
    metadata_json: str | None = None,
) -> str:
    """Replace memory text and/or metadata in place. Prefer this over delete+add.

    Pass metadata_json to patch tags (e.g. scope standing|wip|historical). Text may be omitted when only metadata changes.
    """
    uid, aid = _scope(None, None)
    debug_request = _debug_request(memory_id=memory_id, text=text, metadata_json=metadata_json)
    metadata = _parse_metadata_json(metadata_json) if metadata_json else None
    if text is None and not metadata:
        return _run_tool(
            "update_memory",
            aid,
            lambda: dump({"error": "text_or_metadata_required"}),
            debug_request=debug_request,
        )

    def work() -> str:
        try:
            with memory_session() as memory:
                kwargs: dict[str, Any] = {}
                if text is not None:
                    kwargs["text"] = text
                if metadata:
                    kwargs["metadata"] = metadata
                result = memory.update(memory_id, **kwargs)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("update_memory", aid, work, debug_request=debug_request)


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

    return _run_tool(
        "delete_memory",
        aid,
        work,
        debug_request=_debug_request(memory_id=memory_id),
    )


@mcp.tool()
def delete_all_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    confirm: bool = False,
) -> str:
    """Delete all memories in scope. Requires confirm=true."""
    uid, aid = _scope(user_id, agent_id)
    debug_request = _debug_request(confirm=confirm)
    if not confirm:
        return _run_tool(
            "delete_all_memories",
            aid,
            lambda: dump({"error": "confirm_required", "hint": "Pass confirm=true to wipe this scope."}),
            debug_request=debug_request,
        )

    def work() -> str:
        try:
            with memory_session() as memory:
                result = memory.delete_all(user_id=uid, agent_id=aid)
        except StoreBusy as exc:
            return _store_busy_payload(exc)
        return dump(result)

    return _run_tool("delete_all_memories", aid, work, debug_request=debug_request)


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

    access_params = _log_params(call_ts=call_ts, helpful=helpful, reason=reason, note=note)
    return _run_tool(
        "rate_memory_call",
        aid,
        work,
        params=access_params,
        debug_request=access_params,
    )


def run_mcp() -> None:
    """Run the stdio MCP server (JSON-RPC over stdin/stdout)."""
    mcp.run(transport="stdio")
