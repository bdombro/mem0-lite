"""Context plugin protocol. Plugins are operator code in this process — not a sandbox."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginRequest:
    """Tool-call context passed to plugins. cwd is a hint, not a load path."""

    tool: str
    user_id: str
    agent_id: str | None
    cwd: str | None = None
    explicit: dict[str, str] = field(default_factory=dict)


@dataclass
class Snapshot:
    """Hot-path context. warning codes: git_unavailable, cwd_not_allowed, git_no_origin."""

    project: str | None = None
    workstream: str | None = None
    source_ref: str | None = None
    scope: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warning: dict[str, Any] | None = None

    def as_tags(self) -> dict[str, str]:
        """Non-empty identity tags for stamping."""
        tags: dict[str, str] = {}
        if self.project:
            tags["project"] = self.project
        if self.workstream:
            tags["workstream"] = self.workstream
        if self.source_ref:
            tags["source_ref"] = self.source_ref
        if self.scope:
            tags["scope"] = self.scope
        return tags


@dataclass
class PromoteOp:
    """Metadata patch for one memory. Runner applies via update(); plugins must not add_memory."""

    memory_id: str
    metadata: dict[str, Any]


class ContextPlugin(ABC):
    """In-tree or extra-dir plugin. Core runner is lock hygiene; plugins may do more."""

    name: str

    @abstractmethod
    def probe(self, request: PluginRequest) -> Snapshot | None:
        """Hot path. None = not applicable. Bundled git: ~2s, no network."""

    def stamp(self, request: PluginRequest, snapshot: Snapshot) -> dict[str, str]:
        """Default add_memory metadata. Explicit request tags still win in core."""
        return snapshot.as_tags()

    def recall_filters(self, snapshot: Snapshot) -> dict[str, Any] | None:
        """Default search/list OR when the agent omitted project/workstream/scope."""
        return None

    def follow_up(self, snapshot: Snapshot, results: Any) -> str | None:
        """At most one hint. No interview questions."""
        return None

    def reconcile(self, snapshot: Snapshot, memories: list[dict[str, Any]]) -> list[PromoteOp]:
        """Background I/O. Return promote ops; never add_memory. Default no-op."""
        return []

    def should_reconcile(self, snapshot: Snapshot) -> bool:
        """If False, the runner skips list+I/O. Default off."""
        return False
