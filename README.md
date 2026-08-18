# mem0-lite

Self-hosted memory for coding agents via MCP. No Docker. No Mem0 Platform. No REST daemon.

The MCP wraps `mem0ai` `Memory()`, with vectors on disk under `~/.mem0/qdrant`. Tool calls stay in-process. The sidecar keeps Qdrant open; a second MCP on the same dir yields via `lite.lock` / `lite.want` — no extra daemon.

## Why this exists

Official Mem0 gives you three things that do not compose for a local MCP setup:


| Official path                | Problem here                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------- |
| Platform MCP (`mcp.mem0.ai`) | Memory leaves your machine, costs money.                                         |
| OSS REST server              | Docker + Postgres; Heavy, hard to maintain, kills battery on non-Linux machines. |
| `mem0-cli`                   | Client only, does not include server                                             |


mem0-lite is a *lite*weight local Python app that exposes the OSS mem0 library as an MCP service.

## What you get

- **MCP tools** — `add_memory`, `search_memories`, `get_memory_by_id`, `list_memories`, `update_memory`, `delete_memory`, `delete_all_memories` (how to call them is in the tool docs)
- **Local store** — Qdrant on disk (`on_disk=True`), not `/tmp`, with concurrency coordination
- **When to use them** — thin [AGENTS.md pointer](#agentsmd)
- **MCP registration** — [uv launch snippet](#mcp-registration)

### What it stores

vector db, history, config, access log, `lite.lock`, and `lite.want` in `~/.mem0`

## Install

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+, and `OPENAI_API_KEY` (or Ollama — see [architecture](docs/architecture.md)).

1. Clone this repo.
2. Register the MCP server with your host ([snippet below](#mcp-registration)). Set `OPENAI_API_KEY` in the host config or your environment; do not commit it.
3. Paste the [AGENTS.md pointer](#agentsmd).

### Env

The MCP reads these from the process environment. If a variable is set in your system environment and the MCP is not running in a sandbox, the host passes it through — you do not need to duplicate it in the host's `env` block. Values in that `env` block override the system environment.

- `OPENAI_API_KEY` — required unless using Ollama (see below)
- `MEM0_DIR` — data directory (default `~/.mem0`)
- `MEM0_LITE_USER_ID` — default user scope (default `$USER`)
- `MEM0_LITE_AGENT_ID` — optional agent scope
- `MEM0_LITE_LOCK_TIMEOUT` — store lock wait in seconds (default `30`)
- `MEM0_LITE_LLM_PROVIDER` — e.g. `ollama` (default: OpenAI via mem0ai)
- `MEM0_LITE_LLM_MODEL` — LLM model name (default `llama3.2` with Ollama)
- `MEM0_LITE_EMBEDDER_PROVIDER` — e.g. `openai` or `ollama`
- `MEM0_LITE_EMBEDDER_MODEL` — embedder model (default `nomic-embed-text` with Ollama)

Full defaults: [architecture](docs/architecture.md). Opt-in rating: [Feedback mode](#feedback-mode) (`MEM0_LITE_FEEDBACK_MODE`, default off).

### MCP registration

Merge into your MCP host's config. Cursor: `~/.cursor/mcp.json` or project `.cursor/mcp.json`. Replace the directory path.

```json
{
  "mcpServers": {
    "mem0-lite": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/TO/mem0-lite", "mem0-lite", "mcp"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```

### AGENTS.md

Paste into `AGENTS.md` (or user rules). How to call tools is in the MCP schemas. This is *when*.

```md
## Memory

MCP `mem0-lite` is registered. Search at task start, context switch, or when the user references past work. After the reply, write only if a new agent would benefit in days/weeks (future utility, novelty, factual, no secrets). Do not announce recall. Prefer `update_memory`. Most turns write nothing.
```

## Metrics

Every tool call appends one line to `~/.mem0/access-log.jsonl` (tool, agent, connection reuse, lock wait, duration). Summarize:

```bash
uv run python scripts/access-report.py
```

## Feedback mode

Tracks effectiveness of the memory store.

When on, tool responses include a `ts` field, which can be used to rate a response using the `rate_memory_call` tool.

When on (`MEM0_LITE_FEEDBACK_MODE=1`):

- Retrieval responses include `ts`
- Agents can call `rate_memory_call(call_ts, helpful, reason)` after a useful hit, a miss, or noise
- Ratings append to `~/.mem0/feedback.jsonl`
- `scripts/access-report.py` joins ratings to access-log `ts` and reports coverage, helpful rate, and feedback latency (`ts − call_ts`)

Enable in the MCP host `env` block (do not put this in the default registration snippet):

```json
"env": {
  "OPENAI_API_KEY": "sk-...",
  "MEM0_LITE_FEEDBACK_MODE": "1"
}
```

If you enable it, add this to AGENTS.md.

```md
After a retrieval call, if you used a hit, clearly missed a fact, or got noise, call rate_memory_call with that response's ts.
```

`reason` is one of: `used` | `empty_ok` | `miss` | `noise` | `stale` | `bad_query`. Skip empty-and-expected results.

## Docs


| Page                                                                     | Contents                                               |
| ------------------------------------------------------------------------ | ------------------------------------------------------ |
| [Architecture](docs/architecture.md)                                     | Process model, tools, data layout                      |
| [Alternative Mem0 architectures](docs/alternative-mem0-architectures.md) | Platform, Docker REST, SDK-in-process, this repo       |
| [Alternative storage](docs/alternative-memory-storage.md)                | Files, SQLite, vector DBs, hosted memory               |
| [Decisions](docs/decisions.md)                                           | Why MCP, why `infer=false`, why not REST               |
| [Footguns](docs/footguns.md)                                             | Keys in git, `/tmp` Qdrant, CLI-as-tool, query rewrite |


## Not this repo

- A second Mem0 server implementation
- A Platform-compatible HTTP API
- A replacement for `mem0-cli`
- Graph memory (Platform-only in practice)

This repo is MIT. `mem0ai` remains Apache-2.0. This wrapper is yours to keep local.