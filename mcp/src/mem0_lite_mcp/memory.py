"""Lazy singleton Memory() with on-disk Qdrant."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from mem0 import Memory

_lock = threading.Lock()
_memory: Memory | None = None


def data_dir() -> Path:
    raw = os.environ.get("MEM0_LITE_DATA_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / ".mem0-lite"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_user_id() -> str:
    return os.environ.get("MEM0_LITE_USER_ID") or os.environ.get("USER") or "default"


def default_agent_id() -> str | None:
    return os.environ.get("MEM0_LITE_AGENT_ID") or None


def _build_config() -> dict[str, Any]:
    qdrant_path = data_dir() / "qdrant"
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


def get_memory() -> Memory:
    global _memory
    if _memory is not None:
        return _memory
    with _lock:
        if _memory is None:
            _memory = Memory.from_config(_build_config())
        return _memory
