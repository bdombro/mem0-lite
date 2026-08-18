# Git context plugin

Plugin for [mem0-lite](https://github.com/bdombro/mem0-lite). When an MCP tool receives `cwd`, this plugin runs a **local, allowlisted** git snapshot and maps the checkout to memory metadata. It does not run a shell, does not store git output as memories, and does not touch the network on the search/add hot path.

Agents often forget to pass `project` or `workstream`. Passing `cwd` from the open checkout is enough for this plugin to stamp tags and apply default recall.

## What it writes

Metadata stamped on `add_memory` (explicit tool args still win):


| Field        | Source                                                                     |
| ------------ | -------------------------------------------------------------------------- |
| `project`    | Canonical origin URL (`github.com/org/repo`), never a local path           |
| `workstream` | Jira-style ticket from the branch name (`PROJ-123`), else `branch:<name>`  |
| `source_ref` | Exact current branch (for later merge reconciliation)                      |
| `scope`      | `standing` on `main` / `master` / the repo default branch; otherwise `wip` |


If `origin` is missing or resolves to a local path, `project` is omitted and the response may include a `warning` with code `git_no_origin`.

## Default recall

When the agent omits `project`, `workstream`, and `scope`, search and list apply:

- `scope=standing` and `project=<current>`, **or**
- `scope=wip`, `workstream=<current>`, and `project=<current>`

See [scope.py](../../../mem0_lite/scope.py) for the filter shape.

## Hot path (~2s, no network)

Runs on `add_memory`, `search_memories`, and `list_memories` when `cwd` is set:

- `git rev-parse --show-toplevel`
- `git remote get-url origin`
- `git branch --show-current`
- `git status --porcelain=v1 -b`
- `git rev-parse --verify` for `main`, then `master`

**Never** on the hot path: `git fetch`, `git diff`, `gh`, or any write.

## Background reconcile (opt-in)

After search/list return, the core runner may call `reconcile` **only** when one of these env flags is set (all default **off**):


| Variable                  | Effect                                                                     |
| ------------------------- | -------------------------------------------------------------------------- |
| `MEM0_LITE_GIT_FETCH=1`   | `git fetch` (prompts disabled) before merge checks                         |
| `MEM0_LITE_GIT_GH=1`      | `gh pr list --head <branch> --state merged` as a merge signal              |
| `MEM0_LITE_GIT_PROMOTE=1` | Patch matching WIP memories to `scope=standing` when a branch looks merged |


Promotion rules:

- Only `scope=wip` memories in the current `project` whose `source_ref` is merged **and** is not the current HEAD.
- Squash merges: prefer a merged GitHub PR (`gh`) over `merge-base --is-ancestor` alone.
- Auth failures on fetch or `gh` are skipped, not retried.
- Reconcile I/O runs outside `memory_session()`; store writes use the core runner ([D10](../../../docs/decisions.md#d10--keep-open-embedded-qdrant--cooperative-yield-no-store-daemon)).

With all three flags off, the plugin still stamps and filters from the hot-path snapshot. Search/list may include one `follow_up` suggesting manual `update_memory` for stale WIP.

## Confinement

- `cwd` must be a real directory inside the git toplevel; `cwd=/` is refused.
- Optional `MEM0_LITE_GIT_ROOTS` (path-separated): the toplevel must lie under one of those paths.
- Subprocess only, fixed argv allowlists, no `shell=True`, no agent-supplied argv.
- Git or `gh` output is never passed to `add_memory`.

Probe failures set a structured `warning` (`git_unavailable`, `cwd_not_allowed`, …). Search and list still run without inferred project filters.

## Environment


| Variable                    | Default | Role                             |
| --------------------------- | ------- | -------------------------------- |
| `MEM0_LITE_GIT_ROOTS`       | unset   | Allowlist git toplevels          |
| `MEM0_LITE_GIT_FETCH`       | off     | Background fetch                 |
| `MEM0_LITE_GIT_GH`          | off     | Merged PR signal via `gh`        |
| `MEM0_LITE_GIT_PROMOTE`     | off     | Auto `wip` → `standing`          |
| `MEM0_LITE_PLUGINS_DISABLE` | empty   | Set to `git` to skip this plugin |


Set `GIT_TERMINAL_PROMPT=0` and `GH_PROMPT_DISABLED=1` in the subprocess environment so auth prompts do not hang the MCP.

## Install

Bundled plugins live in this repo under `plugins/<name>/`. They are **not** loaded from the checkout. `just setup` symlinks them into `$MEM0_DIR/plugins/` (default `~/.mem0/plugins/`). Custom plugins use the same layout there.

## Disable

In the MCP host `env` block:

```json
"MEM0_LITE_PLUGINS_DISABLE": "git"
```

Or remove `cwd` from tool calls and pass `project` / `workstream` explicitly.

## Layout

```
plugins/git/              # bundled source (repo root)
├── README.md             # this file
├── __init__.py           # GitPlugin and helpers
└── git_test.py           # unit tests (colocated)

~/.mem0/plugins/git/      # runtime (symlink from just setup)
```



## Further reading

- [D11 — bundled git probe](../../../docs/decisions.md#d11--bundled-git-is-an-allowlisted-probe-not-a-shell)
- [Architecture — project / workstream / scope](../../../docs/architecture.md#project--workstream--scope)
- [Footguns — cwd, fetch auth, plugins](../../../docs/footguns.md)

