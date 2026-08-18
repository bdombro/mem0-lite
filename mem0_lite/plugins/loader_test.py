"""Plugin loader. One subdir per plugin under mem0_dir()/plugins — never loose files at the root."""

from __future__ import annotations

from mem0_lite.plugins.loader import loaded_plugins, reset_plugins

_DEMO = """
from mem0_lite.plugins.base import ContextPlugin, PluginRequest, Snapshot

class DemoPlugin(ContextPlugin):
    name = "demo"
    def probe(self, request: PluginRequest) -> Snapshot | None:
        return None
"""

_EVIL = """
from mem0_lite.plugins.base import ContextPlugin, PluginRequest, Snapshot

class EvilPlugin(ContextPlugin):
    name = "evil"
    def probe(self, request: PluginRequest) -> Snapshot | None:
        return Snapshot(project="pwned")
"""


def test_discovers_bundled_git() -> None:
    assert {plugin.name for plugin in loaded_plugins()} >= {"git"}


def test_disable_git(monkeypatch) -> None:
    monkeypatch.setenv("MEM0_LITE_PLUGINS_DISABLE", "git")
    reset_plugins()
    assert "git" not in {plugin.name for plugin in loaded_plugins()}


def test_loads_user_plugin(mem0_home) -> None:
    demo = mem0_home / "plugins" / "demo"
    demo.mkdir()
    (demo / "plugin.py").write_text(_DEMO, encoding="utf-8")
    reset_plugins()
    names = {plugin.name for plugin in loaded_plugins()}
    assert names >= {"demo", "git"}


def test_ignores_loose_py_at_plugins_root(mem0_home) -> None:
    (mem0_home / "plugins" / "flat.py").write_text(_DEMO, encoding="utf-8")
    reset_plugins()
    assert "demo" not in {plugin.name for plugin in loaded_plugins()}


def test_does_not_load_from_workspace_cwd(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    evil = workspace / "evil"
    evil.mkdir(parents=True)
    (evil / "plugin.py").write_text(_EVIL, encoding="utf-8")
    reset_plugins()
    assert "evil" not in {plugin.name for plugin in loaded_plugins()}
