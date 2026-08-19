# Debug log

Opt-in request/response logging for diagnosing recall and writes. When `MEM0_LITE_DEBUG` is on, each MCP tool call appends one JSON line to `debug.jsonl`. The flag is off by default. Metrics stay in `access-log.jsonl`; this log holds the payload.

The host already shows tool arguments in the chat. This log is for what the host does not keep: stamped tags, the filters actually passed to Mem0, `warning` / `follow_up` / `store_busy`, and a record that survives after the session ends.

## Enable

Set the flag in the MCP host `env` block. Do not add it to the [README registration snippet](../README.md#mcp-registration). Relaunch the host so the sidecar picks up the change.

```json
"env": {
  "MEM0_LITE_DEBUG": "1"
}
```

Same truthy values as other flags: `1`, `true`, `yes`, `on` (case-insensitive). The log path is `$MEM0_DIR/debug.jsonl` (default `~/.mem0/debug.jsonl`).

Nothing is written to stdout or stderr. Stdio is the MCP protocol.

## Record format

One compact JSON object per line. `ts` matches the corresponding `access-log.jsonl` line, so you can join duration and lock wait with the payload.

```json
{
  "ts": "2026-08-18T14:00:00.000000+00:00",
  "tool": "search_memories",
  "agent_id": null,
  "request": {
    "query": "pnpm typescript",
    "memory_type": "decision",
    "top_k": 10,
    "project": "github.com/bdombro/mem0-lite",
    "filters": {
      "user_id": "briandombrowski",
      "type": "decision",
      "project": "github.com/bdombro/mem0-lite"
    }
  },
  "response": {"results": []},
  "duration_ms": 42.1
}
```

Unset optional arguments are omitted. The tool JSON body is parsed into `response` when possible. If the handler raises before returning, `response` is `null`.

Background git reconcile logs as `tool: plugin_reconcile` when `MEM0_LITE_GIT_FETCH`, `MEM0_LITE_GIT_GH`, or `MEM0_LITE_GIT_PROMOTE` is on.

## Effective request

`request` is the **effective** call, not only the agent’s arguments.

| Tool | Extra fields |
|---|---|
| `add_memory` | Agent args including `text` and `infer`, plus `tags` after plugins stamp |
| `search_memories` / `list_memories` | Agent args, plus `filters` actually passed to Mem0 (`user_id`, `type`, default recall OR when `project` is known) |
| `get_memory_by_id` / `update_memory` / `delete_memory` | `memory_id`; update also logs `text` / `metadata_json` |
| `delete_all_memories` | `confirm` |
| `rate_memory_call` | `call_ts`, `helpful`, `reason`, `note` |
| `plugin_reconcile` | `plugin`, applied ids, `error` |

Default recall is easy to miss: the agent omitted `project` / `workstream` / `scope`, a plugin inferred them from `cwd`, and search ran `standing+project` OR `wip+workstream+project`. Those filters show up here even when they were not in the tool call.

## Truncation

The file is append-only. Bodies are clipped so a long `list_memories` cannot grow without bound.

| Limit | Default | Effect |
|---|---|---|
| Per string | 2048 characters | Suffix `...truncated` |
| Per list | 20 items | Final element `{"_omitted": N}` |
| Per line | 32768 characters | `response` becomes `{"_truncated": true, "preview": "..."}` |

## Inspect

```bash
# Searches, with the filters Mem0 actually used
jq 'select(.tool=="search_memories") | {ts, query: .request.query, filters: .request.filters}' ~/.mem0/debug.jsonl

# Adds, with stamped tags
jq 'select(.tool=="add_memory") | {ts, text: .request.text, tags: .request.tags}' ~/.mem0/debug.jsonl
```

`mem0-lite log getByTs` and `getColumn` can read this file. `log report` does not.

## Safety

`debug.jsonl` contains memory text: facts you stored, facts you retrieved, paths in `cwd`. Treat it as a secrets dump. Do not commit it, paste it into a ticket, or enable the flag in a shared host config.

Delete the file when you are done, or wipe the store (`just wipe` — stop MCP hosts first). Leaving the flag on will grow the file for as long as the sidecar runs.

## Related

- [Architecture](architecture.md) — data dir and runtime table
- [Footguns](footguns.md) — secrets in host config; debug log as a dump
- [README Metrics](../README.md#metrics) — `mem0-lite log report` / `getByTs` / `getColumn`
