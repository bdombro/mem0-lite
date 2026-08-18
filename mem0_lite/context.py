"""Glue: plugin probe/stamp/recall, follow_up pick, envelope."""

from __future__ import annotations

from typing import Any

from mem0_lite.plugins.base import ContextPlugin, PluginRequest, Snapshot
from mem0_lite.plugins.loader import loaded_plugins
from mem0_lite.plugins.runner import schedule_reconcile
from mem0_lite.scope import (
    agent_passed_scope_filters,
    default_recall_filters,
    explicit_tags,
    extra_metadata_filters,
    merge_tags,
)


def gather_context(
    *,
    tool: str,
    user_id: str,
    agent_id: str | None,
    cwd: str | None = None,
    project: str | None = None,
    repo: str | None = None,
    workstream: str | None = None,
    source_ref: str | None = None,
    scope: str | None = None,
) -> tuple[dict[str, str], Snapshot | None, list[tuple[ContextPlugin, Snapshot]], dict[str, Any] | None]:
    """Probe plugins, merge tags (explicit wins). Returns tags, first snap, pairs, warning."""
    explicit = explicit_tags(
        project=project,
        repo=repo,
        workstream=workstream,
        source_ref=source_ref,
        scope=scope,
    )
    request = PluginRequest(
        tool=tool,
        user_id=user_id,
        agent_id=agent_id,
        cwd=cwd,
        explicit=explicit,
    )
    pairs: list[tuple[ContextPlugin, Snapshot]] = []
    stamped: dict[str, str] = {}
    warning: dict[str, Any] | None = None
    first: Snapshot | None = None
    for plugin in loaded_plugins():
        try:
            snapshot = plugin.probe(request)
        except Exception:
            warning = {"code": "plugin_error", "plugin": plugin.name}
            continue
        if snapshot is None:
            continue
        if first is None:
            first = snapshot
        pairs.append((plugin, snapshot))
        if snapshot.warning:
            warning = snapshot.warning
        stamped = merge_tags(stamped, plugin.stamp(request, snapshot))
    tags = merge_tags(explicit, stamped)
    return tags, first, pairs, warning


def recall_extra(
    *,
    user_id: str,
    agent_id: str | None,
    cwd: str | None,
    project: str | None,
    repo: str | None,
    workstream: str | None,
    scope: str | None,
    memory_type: str | None,
) -> tuple[dict[str, Any], Snapshot | None, list[tuple[ContextPlugin, Snapshot]], dict[str, Any] | None]:
    """Filters beyond user/agent/type: explicit AND, or plugin default OR when project known."""
    tags, snapshot, pairs, warning = gather_context(
        tool="recall",
        user_id=user_id,
        agent_id=agent_id,
        cwd=cwd,
        project=project,
        repo=repo,
        workstream=workstream,
        scope=scope,
    )
    extra: dict[str, Any] = {}
    if memory_type:
        extra["type"] = memory_type
    if agent_passed_scope_filters(project, repo, workstream, scope):
        extra.update(extra_metadata_filters(tags))
        return extra, snapshot, pairs, warning
    applied = False
    for plugin, snap in pairs:
        filters = plugin.recall_filters(snap)
        if filters:
            extra.update(filters)
            applied = True
            break
    if not applied and tags.get("project"):
        extra.update(default_recall_filters(tags["project"], tags.get("workstream")))
    return extra, snapshot, pairs, warning


def pick_follow_up(
    pairs: list[tuple[ContextPlugin, Snapshot]],
    results: Any,
    warning: dict[str, Any] | None,
) -> str | None:
    """At most one follow_up. First plugin that returns a string wins."""
    for plugin, snapshot in pairs:
        if warning and snapshot.warning is None:
            snapshot.warning = warning
        hint = plugin.follow_up(snapshot, results)
        if hint:
            return hint
    return None


def envelope(
    result: Any,
    *,
    warning: dict[str, Any] | None = None,
    follow_up: str | None = None,
) -> dict[str, Any]:
    """Search/list body plus optional warning/follow_up."""
    if isinstance(result, dict):
        body = dict(result)
    else:
        body = {"results": result}
    if warning:
        body["warning"] = warning
    if follow_up:
        body["follow_up"] = follow_up
    return body


def kick_reconcile(
    pairs: list[tuple[ContextPlugin, Snapshot]],
    *,
    user_id: str,
    agent_id: str | None,
) -> None:
    """Fire-and-forget background promote/fetch. Does not mutate the returned payload."""
    schedule_reconcile(pairs, user_id=user_id, agent_id=agent_id)
