"""Git plugin tests with temp repos. No Memory()."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mem0_lite.plugins.base import PluginRequest, Snapshot
from mem0_lite.plugins.loader import _load_file

_git = _load_file(Path(__file__).resolve().parent / "__init__.py", mod_prefix="mem0_lite_ext_plugins.git")
GitAllowlistError = _git.GitAllowlistError
GitPlugin = _git.GitPlugin
GitProbeError = _git.GitProbeError
confine_cwd = _git.confine_cwd
decide_promotes = _git.decide_promotes
normalize_origin_url = _git.normalize_origin_url
snapshot_from_git = _git.snapshot_from_git
validate_git_argv = _git.validate_git_argv
workstream_from_branch = _git.workstream_from_branch


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(root: Path, *, branch: str = "main", origin: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", branch)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README").write_text("x\n", encoding="utf-8")
    _git(root, "add", "README")
    _git(root, "commit", "-m", "init")
    if origin:
        _git(root, "remote", "add", "origin", origin)
    return root


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("git@github.com:org/repo.git", "github.com/org/repo"),
        ("https://github.com/org/repo.git", "github.com/org/repo"),
        ("ssh://git@github.com/org/repo.git", "github.com/org/repo"),
        ("/tmp/local-repo", ""),
        ("file:///tmp/local-repo", ""),
    ],
)
def test_normalize_origin_url(url: str, want: str) -> None:
    assert normalize_origin_url(url) == want


def test_ticket_from_branch_keeps_source_ref() -> None:
    snap = snapshot_from_git(
        origin="https://github.com/org/repo.git",
        branch="feat/PROJ-99-auth",
        default_branch="main",
    )
    assert snap.project == "github.com/org/repo"
    assert snap.workstream == "PROJ-99"
    assert snap.source_ref == "feat/PROJ-99-auth"
    assert snap.scope == "wip"
    assert workstream_from_branch("feat/auth") == "branch:feat/auth"


def test_default_branch_is_standing() -> None:
    snap = snapshot_from_git(origin="https://github.com/o/r.git", branch="main", default_branch="main")
    assert snap.scope == "standing"


@pytest.mark.parametrize(
    ("argv", "allow_fetch"),
    [
        (("diff",), False),
        (("diff", "--stat"), False),
        (("fetch",), False),
        (("checkout", "main"), False),
        (("merge-base", "--is-ancestor", "--upload-pack", "main"), True),
    ],
)
def test_allowlist_refuses(argv: tuple[str, ...], allow_fetch: bool) -> None:
    with pytest.raises(GitAllowlistError):
        validate_git_argv(argv, allow_fetch=allow_fetch)


def test_allowlist_hot_path_and_bg_fetch() -> None:
    validate_git_argv(("status", "--porcelain=v1", "-b"))
    validate_git_argv(("fetch",), allow_fetch=True)
    validate_git_argv(("merge-base", "--is-ancestor", "feat/x", "main"), allow_fetch=True)


def test_probe_stamps_feature_branch(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "r", origin="git@github.com:org/mem.git")
    _git(repo, "checkout", "-b", "feat/ABC-12-login")
    snap = GitPlugin().probe(PluginRequest(tool="add", user_id="u", agent_id=None, cwd=str(repo)))
    assert snap is not None
    assert snap.warning is None
    assert snap.project == "github.com/org/mem"
    assert snap.workstream == "ABC-12"
    assert snap.source_ref == "feat/ABC-12-login"
    assert snap.scope == "wip"


def test_cwd_outside_roots_rejected(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo(tmp_path / "repo", origin="https://github.com/org/r.git")
    monkeypatch.setenv("MEM0_LITE_GIT_ROOTS", str(tmp_path / "other"))
    with pytest.raises(GitProbeError) as exc:
        confine_cwd(repo, repo)
    assert exc.value.code == "cwd_not_allowed"


def test_non_git_cwd(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    snap = GitPlugin().probe(PluginRequest(tool="add", user_id="u", agent_id=None, cwd=str(empty)))
    assert snap is not None
    assert snap.warning and snap.warning.get("code") == "git_unavailable"
    assert not snap.as_tags()


def test_slash_cwd_not_allowed() -> None:
    with pytest.raises(GitProbeError) as exc:
        confine_cwd(Path("/"), Path("/"))
    assert exc.value.code == "cwd_not_allowed"


def test_promote_merged_not_head() -> None:
    snap = Snapshot(project="p", source_ref="feat/current", scope="wip")
    memories = [
        {"id": "a", "metadata": {"scope": "wip", "project": "p", "source_ref": "feat/old"}},
        {"id": "b", "metadata": {"scope": "wip", "project": "p", "source_ref": "feat/current"}},
        {"id": "c", "metadata": {"scope": "historical", "project": "p", "source_ref": "feat/old"}},
        {"id": "d", "metadata": {"scope": "wip", "project": "p"}},
    ]
    ops = decide_promotes(snap, memories, {"feat/old"})
    assert [op.memory_id for op in ops] == ["a"]
    assert ops[0].metadata == {"scope": "standing"}


def test_promote_gated_off_is_noop(tmp_path: Path) -> None:
    plugin = GitPlugin()
    snap = Snapshot(project="p", extra={"toplevel": str(tmp_path)})
    assert not plugin.should_reconcile(snap)
    assert plugin.reconcile(snap, []) == []


def test_follow_up_does_not_add_memory() -> None:
    hint = GitPlugin().follow_up(
        Snapshot(project="p", source_ref="main", scope="standing"),
        {"results": [{"id": "m1", "metadata": {"scope": "wip", "source_ref": "feat/x"}}]},
    )
    assert hint is not None
    assert "m1" in hint
    assert "add_memory" not in hint
