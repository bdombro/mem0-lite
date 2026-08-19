"""Pytest fixtures. Every test gets a temp MEM0_DIR — never ~/.mem0."""

from __future__ import annotations

from pathlib import Path

import pytest

from mem0_lite.plugins.loader import reset_plugins

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PLUGINS = REPO_ROOT / "plugins"


def _link_bundled_plugins(plugins_root: Path) -> None:
    if not BUNDLED_PLUGINS.is_dir():
        return
    for plugin_dir in sorted(BUNDLED_PLUGINS.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        target = plugins_root / plugin_dir.name
        if target.exists():
            continue
        target.symlink_to(plugin_dir.resolve(), target_is_directory=True)


@pytest.fixture(autouse=True)
def mem0_home(tmp_path, monkeypatch):
    """Isolate store, plugin cache, and git/plugin env for one test."""
    home = tmp_path / "mem0"
    home.mkdir()
    plugins = home / "plugins"
    plugins.mkdir()
    _link_bundled_plugins(plugins)
    monkeypatch.setenv("MEM0_DIR", str(home))
    monkeypatch.setenv("MEM0_TELEMETRY", "False")
    monkeypatch.setenv("MEM0_LITE_LOCK_TIMEOUT", "5")
    for key in (
        "MEM0_LITE_DATA_DIR",
        "MEM0_LITE_PLUGINS_DISABLE",
        "MEM0_LITE_GIT_ROOTS",
        "MEM0_LITE_GIT_FETCH",
        "MEM0_LITE_GIT_GH",
        "MEM0_LITE_GIT_PROMOTE",
        "MEM0_LITE_DEBUG",
        "MEM0_LITE_FEEDBACK_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_plugins()
    yield home
    reset_plugins()
