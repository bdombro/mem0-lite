<!--
Paste the section below into AGENTS.md.

Register MCP (Cursor ~/.cursor/mcp.json or project .cursor/mcp.json):

{
  "mcpServers": {
    "mem0-lite": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/briandombrowski/dev/bdombro/mem0-lite/mcp", "mem0-lite-mcp"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}

Env: OPENAI_API_KEY (required unless Ollama). Optional: MEM0_LITE_USER_ID, MEM0_LITE_AGENT_ID, MEM0_LITE_DATA_DIR (~/.mem0-lite), MEM0_LITE_LLM_PROVIDER=ollama, MEM0_LITE_LLM_MODEL, MEM0_LITE_EMBEDDER_PROVIDER, MEM0_LITE_EMBEDDER_MODEL.
-->


## Mem0 (local)

Persistent memory via MCP tools: `add_memory`, `search_memories`, `get_memory_by_id`, `list_memories`, `update_memory`, `delete_memory`.

Act on recalled facts. Do not announce memory ("I remember that…"). Most turns need zero writes.

### Recall

Search at task start, context switch, or when the user references past work. Skip if recalled results already cover the topic.

Rewrite queries: 3–6 keywords in storage language (third-person facts). Drop pronouns, question words, and the raw user message.

Run 2–4 parallel `search_memories` when useful:

| Angle | `memory_type` |
|---|---|
| architecture / "we decided" | `decision` |
| coding patterns / conventions | `convention` |
| pitfalls / "don't try" | `anti_pattern` |
| catch-all | omit |

Dedup by id. Cap at ~10. Silent if empty.

### Capture

After the reply, ask: would a **new** agent with no context benefit from this in days/weeks? If no → skip.

All four gates must pass:

1. **Future utility** — identity, prefs with rationale, decisions, conventions, standing rules, stack, anti-patterns, project status. Fail: tool output, one-shot commands, small talk, transient state.
2. **Novelty** — already known unchanged → skip. Materially changed → `update_memory`. Cosmetic rephrase → skip.
3. **Factual** — concrete names, configs, choices with why. Fail: vibes, questions, "sure I can help".
4. **Safe** — never store secrets, tokens, keys, webhook URLs with creds. Store "X was configured", not the value.

Store with `infer=false`. Third person, named entities (no "they"/"it"), 15–50 words, self-contained. Group facts about the same entity into one memory. Time-sensitive facts: "As of YYYY-MM-DD, …".

`memory_type`: `decision` | `convention` | `anti_pattern` | `user_preference` | `task_learning` | `environmental` | `identity` | `rule` | `project`

Prefer `update_memory` over delete+add. Never store raw diffs, logs, code dumps, or facts the user asked not to remember.
