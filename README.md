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

Requires Python 3.11+, `OPENAI_API_KEY` (or Ollama — see [architecture](docs/architecture.md)), and `just` for the recipes below. `just setup` installs [uv](https://docs.astral.sh/uv/) if it is missing.

1. Clone this repo.
2. `just setup` — installs uv when needed, `uv sync --group dev`, and symlinks bundled plugins into `~/.mem0/plugins/`.
3. Register the MCP server with your host ([snippet below](#mcp-registration)). Set `OPENAI_API_KEY` in the host config or your environment; do not commit it.
4. Paste the [AGENTS.md pointer](#agentsmd).

Common commands:

```bash
just setup    # install uv if needed, uv sync, link plugins into ~/.mem0/plugins
just test     # pytest
just run      # mem0-lite mcp (stdio)
just wipe     # delete ~/.mem0 or $MEM0_DIR — stop MCP hosts first
```



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
- `MEM0_LITE_PLUGINS_DISABLE` — comma-separated plugin names to skip (e.g. `git`)
- `MEM0_LITE_GIT_ROOTS` — pathsep-separated allowlist for git toplevels
- `MEM0_LITE_GIT_FETCH` / `MEM0_LITE_GIT_GH` / `MEM0_LITE_GIT_PROMOTE` — background git (all default off)

Full defaults: [architecture](docs/architecture.md). Opt-in rating: [Feedback mode](#feedback-mode) (`MEM0_LITE_FEEDBACK_MODE`, default off).

Plugins: source in repo `plugins/<name>/` (entry: `plugin.py` or `__init__.py`). Runtime loads only from `$MEM0_DIR/plugins/<name>/` (default `~/.mem0/plugins/`). `just setup` symlinks bundled plugins there. That is code in the MCP process ([D12](docs/decisions.md#d12--plugins-are-operator-code-not-a-sandbox)). Not loaded from the agent workspace.

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

MCP `mem0-lite` is registered. Search at task start, context switch, or when the user references past work. Pass `cwd` when in a checkout (or `project` / `workstream` if already known). Include `memory_type: environmental` when the work will run local tools. After the reply, write only if a new agent would benefit in days/weeks (future utility, novelty, factual, no secrets). Do not announce recall, misses, or “memories didn’t help” — just continue. Prefer `update_memory`. Most turns write nothing. After a missing binary, wrong version, or version-manager PATH hole, write one standing `environmental` fact (how to invoke the tool, not a shell dump). Pass `cwd` so it stamps this repo.
```



## Metrics

Every tool call appends one line to `~/.mem0/access-log.jsonl` (tool, agent, connection reuse, lock wait, duration, `store_count`). Search and list also stamp `scope_count` (memories matching that call's filters). Query logs without opening Qdrant:

```bash
uv run mem0-lite log report                 # access + feedback summary (JSON)
uv run mem0-lite log getByTs <ts>           # access + feedback + debug rows for one ts
uv run mem0-lite log getColumn <column>     # CSV of one field across all logs
```

`getByTs` follows `ts` / `call_ts` links, so a feedback rating timestamp still returns the original tool call. `getColumn` takes a top-level key or dotted path (`tool`, `params.query`, `request.filters.project`) and walks access, then feedback, then debug.

The report includes last/min/max `store_count` and feedback sliced by size at call time (`0`, `1-10`, `11-50`, `51+`). Early `empty_ok` / `miss` at bucket `0` is expected; watch `used` vs `noise` as the corpus grows.



## Feedback mode

Tracks effectiveness of the memory store.

When on, tool responses include a `ts` field, which can be used to rate a response using the `rate_memory_call` tool.

When on (`MEM0_LITE_FEEDBACK_MODE=1`):

- Retrieval responses include `ts`
- Agents can call `rate_memory_call(call_ts, helpful, reason)` after a useful hit, a miss, or noise
- Ratings append to `~/.mem0/feedback.jsonl`
- `mem0-lite log report` joins ratings to access-log `ts` and reports coverage, helpful rate, feedback latency (`ts − call_ts`), and helpful/reason rates by store size at the call

Enable in the MCP host `env` block (do not put this in the default registration snippet):

```json
"env": {
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
| [Decisions](docs/decisions.md)                                           | Why MCP, why `infer=false`, why not REST, D11 git, D12 plugins |
| [Footguns](docs/footguns.md)                                             | Keys in git, `/tmp` Qdrant, CLI-as-tool, query rewrite, plugins |
| [Debug log](docs/debug.md)                                               | `MEM0_LITE_DEBUG`, `debug.jsonl`                       |




## Not this repo

- A second Mem0 server implementation
- A Platform-compatible HTTP API
- A replacement for `mem0-cli`
- Graph memory (Platform-only in practice)

This repo is MIT. `mem0ai` remains Apache-2.0. This wrapper is yours to keep local.