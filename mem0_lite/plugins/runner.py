"""Background reconcile: plugin I/O outside lite.lock, store writes inside."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from mem0_lite.access_log import log_tool_call
from mem0_lite.core import StoreBusy, memory_session
from mem0_lite.debug_log import log_debug_call
from mem0_lite.plugins.base import ContextPlugin, PromoteOp, Snapshot

log = logging.getLogger("mem0_lite.plugins")

_job_lock = threading.Lock()
_job_running = False
_PROMOTE_CAP = 20


def schedule_reconcile(
    plugins: list[tuple[ContextPlugin, Snapshot]],
    *,
    user_id: str,
    agent_id: str | None,
) -> None:
    """Start at most one background job. No-op if a job is already running."""
    if not plugins:
        return
    global _job_running
    with _job_lock:
        if _job_running:
            return
        _job_running = True
    thread = threading.Thread(
        target=_run_job,
        args=(plugins, user_id, agent_id),
        name="mem0-lite-reconcile",
        daemon=True,
    )
    thread.start()


def _run_job(
    plugins: list[tuple[ContextPlugin, Snapshot]],
    user_id: str,
    agent_id: str | None,
) -> None:
    global _job_running
    started = datetime.now(timezone.utc)
    try:
        for plugin, snapshot in plugins:
            _run_one(plugin, snapshot, user_id, agent_id, started)
    finally:
        with _job_lock:
            _job_running = False


def _run_one(
    plugin: ContextPlugin,
    snapshot: Snapshot,
    user_id: str,
    agent_id: str | None,
    started: datetime,
) -> None:
    if not plugin.should_reconcile(snapshot):
        return
    memories: list[dict[str, Any]] = []
    load_error: str | None = None
    if snapshot.project:
        try:
            memories = _list_wip(snapshot.project, user_id, agent_id)
        except StoreBusy:
            load_error = "store_busy"
        except Exception as exc:
            load_error = type(exc).__name__
            log.exception("reconcile list failed for plugin %s", plugin.name)

    ops: list[PromoteOp] = []
    recon_error: str | None = None
    try:
        ops = plugin.reconcile(snapshot, memories)[:_PROMOTE_CAP]
    except Exception as exc:
        recon_error = type(exc).__name__
        log.exception("reconcile I/O failed for plugin %s", plugin.name)

    applied: list[str] = []
    write_error: str | None = None
    if ops:
        try:
            applied = _apply_ops(ops)
        except StoreBusy:
            write_error = "store_busy"
        except Exception as exc:
            write_error = type(exc).__name__
            log.exception("reconcile write failed for plugin %s", plugin.name)

    params = {
        "plugin": plugin.name,
        "ids": applied,
        "error": load_error or recon_error or write_error,
    }
    log_tool_call(
        tool="plugin_reconcile",
        agent_id=agent_id,
        metrics=None,
        duration_ms=0.0,
        timestamp=started,
        params=params,
    )
    log_debug_call(
        ts=started.isoformat(),
        tool="plugin_reconcile",
        agent_id=agent_id,
        request=params,
        response={"ids": applied, "error": load_error or recon_error or write_error},
    )


def _list_wip(project: str, user_id: str, agent_id: str | None) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {"user_id": user_id, "project": project, "scope": "wip"}
    if agent_id:
        filters["agent_id"] = agent_id
    with memory_session() as memory:
        result = memory.get_all(filters=filters, top_k=_PROMOTE_CAP)
    if isinstance(result, dict):
        hits = result.get("results", [])
        return hits if isinstance(hits, list) else []
    return result if isinstance(result, list) else []


def _apply_ops(ops: list[PromoteOp]) -> list[str]:
    applied: list[str] = []
    with memory_session() as memory:
        for op in ops:
            memory.update(op.memory_id, metadata=op.metadata)
            applied.append(op.memory_id)
    return applied
