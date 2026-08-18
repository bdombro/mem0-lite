"""Project/workstream/scope metadata helpers and default recall filters."""

from __future__ import annotations

from typing import Any

SCOPES = ("standing", "wip", "historical")
SCOPE_KEYS = ("project", "workstream", "source_ref", "scope")


def canonical_project(project: str | None, repo: str | None) -> str | None:
    """Prefer project; repo is a tool-arg alias. Do not treat a local path as identity."""
    value = (project or repo or "").strip()
    return value or None


def normalize_scope(scope: str | None) -> str | None:
    """Return a known scope string or None if unset/blank."""
    if scope is None:
        return None
    value = scope.strip().lower()
    return value if value in SCOPES else None


def explicit_tags(
    *,
    project: str | None = None,
    repo: str | None = None,
    workstream: str | None = None,
    source_ref: str | None = None,
    scope: str | None = None,
) -> dict[str, str]:
    """Build metadata from tool args. Empty omitted. Invalid scope omitted."""
    tags: dict[str, str] = {}
    proj = canonical_project(project, repo)
    if proj:
        tags["project"] = proj
    if workstream and workstream.strip():
        tags["workstream"] = workstream.strip()
    if source_ref and source_ref.strip():
        tags["source_ref"] = source_ref.strip()
    normalized = normalize_scope(scope)
    if normalized:
        tags["scope"] = normalized
    return tags


def merge_tags(explicit: dict[str, str], stamped: dict[str, str]) -> dict[str, str]:
    """Explicit tool args win; plugin stamps fill gaps."""
    merged = dict(stamped)
    merged.update(explicit)
    return {key: value for key, value in merged.items() if value}


def default_recall_filters(project: str, workstream: str | None) -> dict[str, Any]:
    """standing+project OR wip+workstream+project. Keys in one dict AND together."""
    standing = {"scope": "standing", "project": project}
    if workstream:
        return {
            "OR": [
                standing,
                {"scope": "wip", "workstream": workstream, "project": project},
            ]
        }
    return standing


def agent_passed_scope_filters(
    project: str | None,
    repo: str | None,
    workstream: str | None,
    scope: str | None,
) -> bool:
    """True when the agent set project/workstream/scope and default OR must not apply."""
    return bool(canonical_project(project, repo) or (workstream and workstream.strip()) or (scope and scope.strip()))


def extra_metadata_filters(tags: dict[str, str]) -> dict[str, str]:
    """AND filters from explicit tags (no default OR). Omits source_ref."""
    extra: dict[str, str] = {}
    for key in ("project", "workstream", "scope"):
        if key in tags:
            extra[key] = tags[key]
    return extra
