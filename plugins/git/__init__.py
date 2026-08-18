"""Bundled git context plugin. D11: allowlisted probes, no shell, never persist git/gh as memories."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mem0_lite.core import env_flag
from mem0_lite.plugins.base import ContextPlugin, PluginRequest, PromoteOp, Snapshot
from mem0_lite.scope import default_recall_filters

HOT_TIMEOUT_S = 2.0
FETCH_TIMEOUT_S = 10.0
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

_HOT_ARGV = frozenset(
    {
        ("rev-parse", "--show-toplevel"),
        ("remote", "get-url", "origin"),
        ("branch", "--show-current"),
        ("status", "--porcelain=v1", "-b"),
        ("rev-parse", "--verify", "main"),
        ("rev-parse", "--verify", "master"),
    }
)
_FETCH_ARGV = frozenset({("fetch",), ("fetch", "origin")})
_FORBIDDEN = frozenset({"diff", "checkout", "reset", "commit", "push", "rebase", "merge", "add", "rm"})


class GitAllowlistError(ValueError):
    """Refused git/gh argv (not in the bundled allowlist)."""


class GitProbeError(RuntimeError):
    """Structured probe failure. code is git_unavailable or cwd_not_allowed."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def git_roots() -> list[Path] | None:
    """MEM0_LITE_GIT_ROOTS (os.pathsep). None means only toplevel confinement."""
    raw = os.environ.get("MEM0_LITE_GIT_ROOTS", "").strip()
    if not raw:
        return None
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()]


def is_under(path: Path, root: Path) -> bool:
    """True if path is root or a descendant."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def confine_cwd(cwd: Path, toplevel: Path) -> None:
    """Refuse cwd=/ and cwd outside toplevel; if roots are set, toplevel must sit under one."""
    cwd_r = cwd.resolve()
    top_r = toplevel.resolve()
    if cwd_r == Path("/"):
        raise GitProbeError("cwd_not_allowed", "cwd is /")
    if not is_under(cwd_r, top_r):
        raise GitProbeError("cwd_not_allowed", "cwd is not inside git toplevel")
    roots = git_roots()
    if roots is not None and not any(is_under(top_r, root) for root in roots):
        raise GitProbeError("cwd_not_allowed", "toplevel is outside MEM0_LITE_GIT_ROOTS")


def normalize_origin_url(url: str) -> str:
    """Canonical host/owner/repo. Never a local filesystem path."""
    text = url.strip()
    if text.startswith("/") or text.startswith("file://") or text.startswith("."):
        return ""
    if text.startswith("git@"):
        _, rest = text.split("@", 1)
        host, _, path = rest.partition(":")
        return _strip_git_suffix(f"{host}/{path}")
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https", "ssh"):
        host = parsed.hostname or ""
        path = unquote(parsed.path.lstrip("/"))
        return _strip_git_suffix(f"{host}/{path}")
    if "://" not in text and "/" in text:
        return _strip_git_suffix(text.removeprefix("/"))
    return _strip_git_suffix(text)


def _strip_git_suffix(value: str) -> str:
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")]
    return value


def workstream_from_branch(branch: str) -> str:
    """Jira-style KEY-123 from the branch name, else branch:<name>."""
    match = TICKET_RE.search(branch)
    if match:
        return match.group(1)
    return f"branch:{branch}"


def _safe_ref(value: str) -> str:
    """Reject refs that look like flags or path tricks before interpolating into argv."""
    if not value or value.startswith("-") or ".." in value or "\x00" in value:
        raise GitAllowlistError(f"unsafe git ref: {value!r}")
    return value


def validate_git_argv(argv: tuple[str, ...], *, allow_fetch: bool = False) -> None:
    """Fixed allowlist. No user argv, no diff/checkout. fetch/merge-base only on the bg path."""
    if not argv:
        raise GitAllowlistError("empty git argv")
    if argv[0] in _FORBIDDEN or "diff" in argv:
        raise GitAllowlistError(f"git argv not allowlisted: {argv}")
    if argv in _HOT_ARGV:
        return
    if argv[0] == "fetch":
        if allow_fetch and argv in _FETCH_ARGV:
            return
        raise GitAllowlistError("git fetch is not allowed on this path")
    if (
        allow_fetch
        and argv[0] == "merge-base"
        and len(argv) == 4
        and argv[1] == "--is-ancestor"
    ):
        _safe_ref(argv[2])
        _safe_ref(argv[3])
        return
    raise GitAllowlistError(f"git argv not allowlisted: {argv}")


def validate_gh_argv(argv: tuple[str, ...]) -> None:
    """Read-only gh pr list --state merged. No create/write."""
    if len(argv) != 8 or argv[:3] != ("pr", "list") or "--state" not in argv or "merged" not in argv:
        raise GitAllowlistError(f"gh argv not allowlisted: {argv}")
    if "create" in argv or "checkout" in argv:
        raise GitAllowlistError(f"gh argv not allowlisted: {argv}")


def run_git(
    cwd: str | Path,
    argv: tuple[str, ...],
    *,
    timeout: float,
    allow_fetch: bool = False,
) -> subprocess.CompletedProcess[str]:
    """subprocess git -C <cwd> with a frozen argv allowlist."""
    validate_git_argv(argv, allow_fetch=allow_fetch)
    resolved = str(Path(cwd).resolve())
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", resolved, *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def run_gh(
    cwd: str | Path,
    argv: tuple[str, ...],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """subprocess gh in the repo with prompts disabled."""
    validate_gh_argv(argv)
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["gh", *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
        cwd=str(Path(cwd).resolve()),
    )


def snapshot_from_git(
    *,
    origin: str | None,
    branch: str | None,
    default_branch: str | None,
) -> Snapshot:
    """Map git facts to tags. Missing origin → no project (never a local path)."""
    snap = Snapshot()
    if origin:
        snap.project = normalize_origin_url(origin) or None
    if branch:
        snap.source_ref = branch
        snap.workstream = workstream_from_branch(branch)
        if default_branch and branch == default_branch:
            snap.scope = "standing"
        elif branch in {"main", "master"}:
            snap.scope = "standing"
        else:
            snap.scope = "wip"
    snap.extra["default_branch"] = default_branch
    return snap


def _hits(results: Any) -> list[dict[str, Any]]:
    if isinstance(results, dict):
        raw = results.get("results", [])
        return raw if isinstance(raw, list) else []
    if isinstance(results, list):
        return results
    return []


def _memory_meta(hit: dict[str, Any]) -> dict[str, Any]:
    meta = hit.get("metadata")
    if isinstance(meta, dict):
        return meta
    return hit


def decide_promotes(
    snapshot: Snapshot,
    memories: list[dict[str, Any]],
    merged_refs: set[str],
) -> list[PromoteOp]:
    """Promote wip whose source_ref is merged and is not the current HEAD."""
    current = snapshot.source_ref
    ops: list[PromoteOp] = []
    for hit in memories:
        meta = _memory_meta(hit)
        if meta.get("scope") != "wip":
            continue
        if snapshot.project and meta.get("project") not in (None, snapshot.project):
            continue
        ref = meta.get("source_ref")
        if not ref or not isinstance(ref, str):
            continue
        if ref == current:
            continue
        if ref not in merged_refs:
            continue
        memory_id = hit.get("id")
        if not memory_id:
            continue
        ops.append(PromoteOp(memory_id=str(memory_id), metadata={"scope": "standing"}))
    return ops


class GitPlugin(ContextPlugin):
    name = "git"

    def probe(self, request: PluginRequest) -> Snapshot | None:
        if not request.cwd:
            return None
        try:
            return self._probe_cwd(request.cwd)
        except GitProbeError as exc:
            return Snapshot(warning={"code": exc.code, "detail": exc.detail})
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return Snapshot(warning={"code": "git_unavailable", "detail": type(exc).__name__})

    def should_reconcile(self, snapshot: Snapshot) -> bool:
        return (
            env_flag("MEM0_LITE_GIT_FETCH")
            or env_flag("MEM0_LITE_GIT_GH")
            or env_flag("MEM0_LITE_GIT_PROMOTE")
        )

    def stamp(self, request: PluginRequest, snapshot: Snapshot) -> dict[str, str]:
        return snapshot.as_tags()

    def recall_filters(self, snapshot: Snapshot) -> dict[str, Any] | None:
        if snapshot.warning or not snapshot.project:
            return None
        return default_recall_filters(snapshot.project, snapshot.workstream)

    def follow_up(self, snapshot: Snapshot, results: Any) -> str | None:
        if snapshot.warning:
            code = snapshot.warning.get("code")
            if code == "cwd_not_allowed":
                return "cwd is outside allowed git roots; recall ran without inferred project filters."
            if code == "git_unavailable":
                return "git probe failed; recall ran without inferred project filters."
            if code == "git_no_origin":
                return "git origin missing; pass project= or add a remote."
        hits = _hits(results)
        wip_ids: list[str] = []
        missing_ref: list[str] = []
        for hit in hits:
            meta = _memory_meta(hit)
            if meta.get("scope") != "wip":
                continue
            memory_id = str(hit.get("id") or "")
            if not memory_id:
                continue
            ref = meta.get("source_ref")
            if not ref:
                missing_ref.append(memory_id)
                continue
            if snapshot.scope == "standing" or (snapshot.source_ref and ref != snapshot.source_ref):
                wip_ids.append(memory_id)
        if missing_ref:
            return (
                f"WIP memories {', '.join(missing_ref[:5])} lack source_ref; "
                "promote or delete manually."
            )
        if wip_ids:
            return (
                f"WIP memories {', '.join(wip_ids[:5])} may be merged; "
                "update_memory scope=standing or delete if the background job has not."
            )
        return None

    def reconcile(self, snapshot: Snapshot, memories: list[dict[str, Any]]) -> list[PromoteOp]:
        fetch_on = env_flag("MEM0_LITE_GIT_FETCH")
        gh_on = env_flag("MEM0_LITE_GIT_GH")
        promote_on = env_flag("MEM0_LITE_GIT_PROMOTE")
        if not (fetch_on or gh_on or promote_on):
            return []
        toplevel = snapshot.extra.get("toplevel")
        if not toplevel:
            return []
        top = Path(str(toplevel))
        if fetch_on:
            try:
                run_git(top, ("fetch",), timeout=FETCH_TIMEOUT_S, allow_fetch=True)
            except (GitAllowlistError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        if not promote_on:
            return []
        refs = {
            str(meta.get("source_ref"))
            for hit in memories
            for meta in [_memory_meta(hit)]
            if meta.get("source_ref")
        }
        refs.discard(str(snapshot.source_ref or ""))
        merged = self._merged_refs(top, snapshot, refs, gh_on=gh_on)
        return decide_promotes(snapshot, memories, merged)

    def _probe_cwd(self, cwd: str) -> Snapshot:
        path = Path(cwd).expanduser()
        if not path.is_dir():
            raise GitProbeError("git_unavailable", "cwd is not a directory")
        try:
            top_run = run_git(path, ("rev-parse", "--show-toplevel"), timeout=HOT_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise GitProbeError("git_unavailable", "git not on PATH") from exc
        if top_run.returncode != 0:
            raise GitProbeError("git_unavailable", "not a git checkout")
        toplevel = Path(top_run.stdout.strip())
        confine_cwd(path, toplevel)
        origin_run = run_git(toplevel, ("remote", "get-url", "origin"), timeout=HOT_TIMEOUT_S)
        origin = origin_run.stdout.strip() if origin_run.returncode == 0 else ""
        branch_run = run_git(toplevel, ("branch", "--show-current"), timeout=HOT_TIMEOUT_S)
        branch = branch_run.stdout.strip() if branch_run.returncode == 0 else ""
        status_run = run_git(toplevel, ("status", "--porcelain=v1", "-b"), timeout=HOT_TIMEOUT_S)
        default_branch = _default_branch(toplevel)
        snap = snapshot_from_git(origin=origin or None, branch=branch or None, default_branch=default_branch)
        snap.extra["toplevel"] = str(toplevel)
        snap.extra["dirty"] = _status_is_dirty(status_run.stdout if status_run.returncode == 0 else "")
        if not snap.project:
            snap.warning = {"code": "git_no_origin"}
        return snap

    def _merged_refs(
        self,
        toplevel: Path,
        snapshot: Snapshot,
        refs: set[str],
        *,
        gh_on: bool,
    ) -> set[str]:
        merged: set[str] = set()
        default = snapshot.extra.get("default_branch") or "main"
        for ref in refs:
            if gh_on and _gh_merged(toplevel, ref):
                merged.add(ref)
                continue
            if _is_ancestor(toplevel, ref, str(default)):
                merged.add(ref)
        return merged


def _status_is_dirty(porcelain: str) -> bool:
    """True when status --porcelain=v1 -b lists any changed paths."""
    lines = [line for line in porcelain.splitlines() if line and not line.startswith("##")]
    return bool(lines)


def _default_branch(toplevel: Path) -> str | None:
    for name in ("main", "master"):
        result = run_git(toplevel, ("rev-parse", "--verify", name), timeout=HOT_TIMEOUT_S)
        if result.returncode == 0:
            return name
    return None


def _is_ancestor(toplevel: Path, source_ref: str, default_branch: str) -> bool:
    try:
        source_ref = _safe_ref(source_ref)
        default_branch = _safe_ref(default_branch)
    except GitAllowlistError:
        return False
    for candidate in (f"origin/{default_branch}", default_branch):
        try:
            result = run_git(
                toplevel,
                ("merge-base", "--is-ancestor", source_ref, candidate),
                timeout=HOT_TIMEOUT_S,
                allow_fetch=True,
            )
        except (GitAllowlistError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            return True
    return False


def _gh_merged(toplevel: Path, source_ref: str) -> bool:
    try:
        source_ref = _safe_ref(source_ref)
    except GitAllowlistError:
        return False
    argv = (
        "pr",
        "list",
        "--head",
        source_ref,
        "--state",
        "merged",
        "--json",
        "number,title,mergedAt",
    )
    try:
        result = run_gh(toplevel, argv, timeout=FETCH_TIMEOUT_S)
    except (GitAllowlistError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    text = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0:
        if "401" in text or "auth" in text.lower() or "HTTP 403" in text:
            return False
        return False
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False
    return isinstance(payload, list) and len(payload) > 0
