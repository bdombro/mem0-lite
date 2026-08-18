# mem0-lite

Self-hosted memory for coding agents. No Docker. No Mem0 Platform. No REST daemon.

Cursor (and any MCP client) talks to a long-lived stdio server. That process holds a single `mem0ai` `Memory()` instance, with vectors on disk under `~/.mem0-lite`. Tool calls stay in-process. Cold start happens once per editor session, not once per recall.

## Why this exists

Official Mem0 gives you three things that do not compose for a local Cursor setup:

| Official path | Problem here |
|---|---|
| Platform MCP (`mcp.mem0.ai`) | Memory leaves your machine |
| OSS REST server | Docker + Postgres; CLI still speaks Platform `/v3` paths |
| `mem0-cli` | Platform client; `exec` per tool call is the wrong hot path |

mem0-lite is the missing shape: **OSS `Memory()` behind MCP**, plus an `AGENTS.md` snippet that tells the model when to search and when to write.

## What you get

- **MCP tools** — `add_memory`, `search_memories`, `get_memory_by_id`, `list_memories`, `update_memory`, `delete_memory`, `delete_all_memories`
- **Local store** — Qdrant on disk (`on_disk=True`), not `/tmp`
- **Agent protocol** — [`mem0-attach.md`](mem0-attach.md) for recall-at-task-start and four-gate capture
- **Drop-in Cursor config** — [`mcp.json`](mcp.json)

Python still runs. It runs as a sidecar Cursor already knows how to keep alive, not as a subprocess per `add`.

## Quick start

```bash
# 1. Clone, then put a real key in mcp.json (do not commit it)
# 2. Point Cursor at this file, or merge it into ~/.cursor/mcp.json
# 3. Paste mem0-attach.md into AGENTS.md
```

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+, and `OPENAI_API_KEY` (or Ollama — see [architecture](docs/architecture.md)).

```json
{
  "mcpServers": {
    "mem0-lite": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/TO/mem0-lite/mcp", "mem0-lite-mcp"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```

## Docs

| Page | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Process model, tools, data layout |
| [Alternative Mem0 architectures](docs/alternative-mem0-architectures.md) | Platform, Docker REST, SDK-in-process, this repo |
| [Alternative storage](docs/alternative-memory-storage.md) | Files, SQLite, vector DBs, hosted memory |
| [Decisions](docs/decisions.md) | Why MCP, why `infer=false`, why not REST |
| [Footguns](docs/footguns.md) | Keys in git, `/tmp` Qdrant, CLI-as-tool, query rewrite |

## Not this repo

- A second Mem0 server implementation
- A Platform-compatible HTTP API
- A replacement for `mem0-cli`
- Graph memory (Platform-only in practice)

Apache-2.0-shaped usage of `mem0ai`; this wrapper is yours to keep local.
