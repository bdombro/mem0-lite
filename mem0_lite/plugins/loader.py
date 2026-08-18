"""Auto-discover ContextPlugin classes under ~/.mem0/plugins/<name>/. Never load from the agent workspace cwd."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from mem0_lite.core import mem0_dir
from mem0_lite.plugins.base import ContextPlugin

log = logging.getLogger("mem0_lite.plugins")

_SKIP_DIRS = frozenset({"__pycache__"})
_ENTRY_NAMES = ("plugin.py", "__init__.py")
_cached: list[ContextPlugin] | None = None


def plugins_dir() -> Path | None:
    """$MEM0_DIR/plugins (default ~/.mem0/plugins) when that directory exists."""
    path = mem0_dir() / "plugins"
    return path if path.is_dir() else None


def disabled_names() -> set[str]:
    """Names listed in MEM0_LITE_PLUGINS_DISABLE (comma-separated)."""
    import os

    raw = os.environ.get("MEM0_LITE_PLUGINS_DISABLE", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def reset_plugins() -> None:
    """Drop the process cache so the next loaded_plugins() rescans."""
    global _cached
    _cached = None


def loaded_plugins() -> list[ContextPlugin]:
    """Installed plugins under mem0_dir()/plugins/<name>/. Cached per process."""
    global _cached
    if _cached is None:
        _cached = _discover()
    return _cached


def _discover() -> list[ContextPlugin]:
    found: dict[str, ContextPlugin] = {}
    for plugin in _iter_installed():
        found[plugin.name] = plugin
    skip = disabled_names()
    return [plugin for name, plugin in sorted(found.items()) if name not in skip]


def _plugin_dirs(root: Path) -> list[Path]:
    """One subdirectory per plugin; ignore loose files at the plugins root."""
    if not root.is_dir():
        return []
    dirs = [path for path in sorted(root.iterdir()) if path.is_dir()]
    return [path for path in dirs if path.name not in _SKIP_DIRS and not path.name.startswith("_")]


def _entry_file(plugin_dir: Path) -> Path | None:
    for name in _ENTRY_NAMES:
        path = plugin_dir / name
        if path.is_file():
            return path
    return None


def _iter_installed() -> list[ContextPlugin]:
    directory = plugins_dir()
    if directory is None:
        return []
    plugins: list[ContextPlugin] = []
    for plugin_dir in _plugin_dirs(directory):
        entry = _entry_file(plugin_dir)
        if entry is None:
            continue
        try:
            module = _load_file(entry, mod_prefix=f"mem0_lite_ext_plugins.{plugin_dir.name}")
        except Exception:
            log.exception("skipping plugin %s", plugin_dir)
            continue
        plugins.extend(_plugins_in_module(module))
    return plugins


def _load_file(path: Path, *, mod_prefix: str) -> object:
    """Import plugin.py or __init__.py from a plugin subdirectory."""
    mod_name = f"{mod_prefix}.{path.stem}" if path.name != "__init__.py" else mod_prefix
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _plugins_in_module(module: object) -> list[ContextPlugin]:
    plugins: list[ContextPlugin] = []
    for value in vars(module).values():
        if not isinstance(value, type):
            continue
        if value is ContextPlugin or not issubclass(value, ContextPlugin):
            continue
        try:
            plugins.append(value())
        except Exception:
            log.exception("skipping plugin class %s", getattr(value, "__name__", value))
    return plugins
