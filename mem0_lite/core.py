"""mem0ai Memory() lifecycle, local paths, and cross-process store coordination."""

from __future__ import annotations

import atexit
import errno
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import portalocker
from mem0 import Memory

# Serializes tool calls within this process (flock is per-process, not per-thread).
_thread_lock = threading.Lock()
# Cross-process exclusive lock on MEM0_DIR while we hold an open Qdrant client.
_portalock: portalocker.Lock | None = None
# Live mem0ai Memory() for this process when we are the store holder.
_memory: Memory | None = None
# Count of in-flight memory_session() calls in this process.
_in_use = 0
# Bumped on open/yield so a session can tell if the holder changed mid-call.
_generation = 0
# Background thread that yields the store when idle and another process wants it.
_watcher: threading.Thread | None = None
# Signals the want-watcher thread to stop on process exit.
_watcher_stop = threading.Event()

# How often the idle watcher polls lite.want.
_WANT_POLL_S = 0.05
# Max slice when blocking on lite.lock; waiter refreshes lite.want each slice.
_WANT_REFRESH_S = 0.1


@dataclass
class SessionMetrics:
    """Store open/reuse, lite.lock wait, and collection counts for the last memory_session()."""

    reused_connection: bool = False
    lock_wait_ms: float = 0.0
    store_count: int | None = None
    scope_count: int | None = None


# Metrics from the most recent memory_session() in this process.
_session_metrics: SessionMetrics | None = None


def last_session_metrics() -> SessionMetrics | None:
    """Metrics from the last memory_session(); None if that call did not open the store."""
    return _session_metrics


def _int_or_none(value: Any) -> int | None:
    """Coerce Qdrant count fields to int; None if missing or invalid."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collection_count(memory: Any) -> int | None:
    """Global Qdrant points_count. None when the client has no collection info."""
    store = getattr(memory, "vector_store", None)
    col_info = getattr(store, "col_info", None)
    if col_info is None:
        return None
    try:
        info = col_info()
    except Exception:
        return None
    n = getattr(info, "points_count", None)
    if n is None and isinstance(info, dict):
        n = info.get("points_count")
    return _int_or_none(n)


def _filtered_count(memory: Any, filters: dict[str, Any]) -> int | None:
    """Exact Qdrant count for mem0 filters. None if the store cannot count."""
    store = getattr(memory, "vector_store", None)
    client = getattr(store, "client", None)
    name = getattr(store, "collection_name", None)
    create_filter = getattr(store, "_create_filter", None)
    if client is None or not name or create_filter is None:
        return None
    try:
        result = client.count(
            collection_name=name,
            count_filter=create_filter(filters),
            exact=True,
        )
    except Exception:
        return None
    return _int_or_none(getattr(result, "count", None))


def record_scope_count(memory: Any, filters: dict[str, Any] | None) -> None:
    """Stamp in-scope count on the live session metrics. Call inside memory_session()."""
    metrics = _session_metrics
    if metrics is None or not filters:
        return
    metrics.scope_count = _filtered_count(memory, filters)


def clear_session_metrics() -> None:
    """Reset session metrics before a tool call that may not use memory_session()."""
    global _session_metrics
    _session_metrics = None


class StoreBusy(RuntimeError):
    """Another process held the store longer than MEM0_LITE_LOCK_TIMEOUT."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"mem0 store busy after {timeout:g}s")


def dump(obj: Any) -> str:
    """Serialize tool results as indented JSON for MCP text responses."""
    return json.dumps(obj, default=str, indent=2)


def mem0_dir() -> Path:
    """Root data directory: qdrant/, history.db, lite.lock, lite.want."""
    raw = os.environ.get("MEM0_DIR") or os.environ.get("MEM0_LITE_DATA_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / ".mem0"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_user_id() -> str:
    """Default mem0 user scope when a tool omits user_id."""
    return os.environ.get("MEM0_LITE_USER_ID") or os.environ.get("USER") or "default"


def default_agent_id() -> str | None:
    """Optional mem0 agent scope from env; None means no agent filter."""
    return os.environ.get("MEM0_LITE_AGENT_ID") or None


def env_flag(name: str) -> bool:
    """True when an env var is a truthy flag (1/true/yes/on)."""
    raw = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def feedback_mode() -> bool:
    """True when MEM0_LITE_FEEDBACK_MODE adds ts to tool responses for agent rating."""
    return env_flag("MEM0_LITE_FEEDBACK_MODE")


def debug_mode() -> bool:
    """True when MEM0_LITE_DEBUG writes request/response to debug.jsonl."""
    return env_flag("MEM0_LITE_DEBUG")


def _lock_timeout() -> float:
    """Seconds to wait on lite.lock before returning store_busy."""
    raw = os.environ.get("MEM0_LITE_LOCK_TIMEOUT", "30")
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return value if value > 0 else 30.0


def _build_config() -> dict[str, Any]:
    """mem0ai Memory.from_config dict: local Qdrant plus optional LLM/embedder overrides."""
    qdrant_path = mem0_dir() / "qdrant"
    qdrant_path.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": str(qdrant_path), "on_disk": True},
        },
    }

    llm_provider = os.environ.get("MEM0_LITE_LLM_PROVIDER")
    if llm_provider:
        llm_cfg: dict[str, Any] = {"model": os.environ.get("MEM0_LITE_LLM_MODEL", "llama3.2")}
        if llm_provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            llm_cfg["api_key"] = os.environ["OPENAI_API_KEY"]
        config["llm"] = {"provider": llm_provider, "config": llm_cfg}

    embedder_provider = os.environ.get("MEM0_LITE_EMBEDDER_PROVIDER")
    if embedder_provider:
        emb_cfg: dict[str, Any] = {
            "model": os.environ.get("MEM0_LITE_EMBEDDER_MODEL", "nomic-embed-text"),
        }
        if embedder_provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            emb_cfg["api_key"] = os.environ["OPENAI_API_KEY"]
        config["embedder"] = {"provider": embedder_provider, "config": emb_cfg}

    return config


def _close_client(store: Any) -> None:
    """Close a mem0 vector store's Qdrant client if present; errors are ignored."""
    client = getattr(store, "client", None)
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        pass


def _release_memory(memory: Memory) -> None:
    """Drop Qdrant handles and SQLite so another process can open the same MEM0_DIR."""
    _close_client(getattr(memory, "vector_store", None))
    telemetry = getattr(memory, "_telemetry_vector_store", None)
    if telemetry is not None and telemetry is not getattr(memory, "vector_store", None):
        _close_client(telemetry)
    entity = getattr(memory, "_entity_store", None)
    if entity is not None and entity is not getattr(memory, "vector_store", None):
        _close_client(entity)
    close = getattr(memory, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            pass


def _open_memory() -> Memory:
    """Construct a new mem0ai Memory() from env-driven config."""
    return Memory.from_config(_build_config())


def _lock_path() -> Path:
    """Cross-process lock file: holder keeps this until yield."""
    return mem0_dir() / "lite.lock"


def _want_path() -> Path:
    """Waiter signal file: contains pid of a process requesting the store."""
    return mem0_dir() / "lite.want"


def _write_want() -> None:
    """Announce this process is waiting for lite.lock (cooperative yield)."""
    _want_path().write_text(f"{os.getpid()}\n", encoding="utf-8")


def _clear_want_if_ours() -> None:
    """Remove lite.want only if it still names this pid (don't clear another waiter)."""
    path = _want_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    except OSError:
        return
    if text != str(os.getpid()):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_is_alive(pid: int) -> bool:
    """Whether pid still exists (signal 0); EPERM counts as alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def _want_is_live() -> bool:
    """True if lite.want names a live process other than us; stale files are removed."""
    path = _want_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not text:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return False
    try:
        pid = int(text)
    except ValueError:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return False
    if pid == os.getpid():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return False
    if _pid_is_alive(pid):
        return True
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return False


def _acquire_lite_lock(timeout: float, deadline: float) -> portalocker.Lock:
    """Block until lite.lock is acquired or StoreBusy; waiter refreshes lite.want while waiting."""
    lock_path = _lock_path()
    lock_path.touch(exist_ok=True)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _clear_want_if_ours()
            raise StoreBusy(timeout)
        _write_want()
        slice_t = min(_WANT_REFRESH_S, remaining)
        lock = portalocker.Lock(
            str(lock_path),
            timeout=slice_t,
            check_interval=min(0.05, slice_t),
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            lock.acquire()
        except portalocker.LockException:
            continue
        _clear_want_if_ours()
        return lock


def _yield_store_locked() -> None:
    """Close Memory/Qdrant and release lite.lock; caller must hold _thread_lock."""
    global _memory, _portalock, _generation
    memory, lock = _memory, _portalock
    _memory = None
    _portalock = None
    _generation += 1
    if memory is not None:
        _release_memory(memory)
    if lock is not None:
        try:
            lock.release()
        except Exception:
            pass


def _maybe_yield_locked() -> None:
    """Yield the store if idle and a live waiter exists; caller holds _thread_lock."""
    if _in_use != 0 or _memory is None:
        return
    if not _want_is_live():
        return
    _yield_store_locked()


def _try_idle_yield() -> None:
    """Attempt yield from the watcher thread without blocking an active tool call."""
    if not _thread_lock.acquire(blocking=False):
        return
    try:
        _maybe_yield_locked()
    finally:
        _thread_lock.release()


def _run_want_watcher() -> None:
    """Daemon loop: yield while idle if another MCP process wrote lite.want."""
    while not _watcher_stop.wait(_WANT_POLL_S):
        _try_idle_yield()


def _ensure_watcher() -> None:
    """Start the idle want-watcher once when this process becomes holder."""
    global _watcher
    thread = _watcher
    if thread is not None and thread.is_alive():
        return
    _watcher_stop.clear()
    thread = threading.Thread(target=_run_want_watcher, name="mem0-lite-want", daemon=True)
    _watcher = thread
    thread.start()


def _become_holder(timeout: float, deadline: float, metrics: SessionMetrics) -> None:
    """Acquire lite.lock, open Memory(), and start the idle watcher."""
    global _memory, _portalock, _generation
    lock_start = time.perf_counter()
    lock = _acquire_lite_lock(timeout, deadline)
    metrics.lock_wait_ms = (time.perf_counter() - lock_start) * 1000
    try:
        memory = _open_memory()
    except BaseException:
        try:
            lock.release()
        except Exception:
            pass
        raise
    _portalock = lock
    _memory = memory
    _generation += 1
    _ensure_watcher()


def _reset_holder() -> None:
    """Force-close Memory and release lite.lock (tests and process shutdown)."""
    global _in_use
    with _thread_lock:
        _in_use = 0
        _yield_store_locked()


@contextmanager
def memory_session() -> Iterator[Memory]:
    """Reuse open Memory() across calls; yield to other processes when lite.want appears."""
    global _in_use, _session_metrics
    metrics = SessionMetrics()
    timeout = _lock_timeout()
    deadline = time.monotonic() + timeout
    with _thread_lock:
        if _memory is None:
            metrics.reused_connection = False
            try:
                _become_holder(timeout, deadline, metrics)
            except StoreBusy:
                _session_metrics = metrics
                raise
        else:
            metrics.reused_connection = True
        gen = _generation
        _in_use += 1
        _session_metrics = metrics
        try:
            assert _memory is not None
            yield _memory
        finally:
            if _memory is not None:
                metrics.store_count = _collection_count(_memory)
            _in_use -= 1
            if _in_use == 0 and _generation == gen:
                _maybe_yield_locked()
            _session_metrics = metrics


# Release Qdrant on exit so a sibling process is not stuck after crash/kill.
atexit.register(_reset_holder)
