# mem0-lite command runner. Requires [just](https://github.com/casey/just). setup installs [uv](https://docs.astral.sh/uv/) if needed.

set shell := ["bash", "-cu"]
set dotenv-load := false

# List recipes
_:
    @just --list

# Install uv if missing, sync runtime + pytest, link bundled plugins into ~/.mem0/plugins
setup:
    command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh; PATH="$HOME/.local/bin:$PATH" uv sync --group dev
    just install-plugins

# Symlink repo plugins/<name>/ into $MEM0_DIR/plugins/<name>/ (default ~/.mem0)
install-plugins:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    dir="${MEM0_DIR:-$HOME/.mem0}/plugins"
    mkdir -p "$dir"
    shopt -s nullglob
    for d in "$root"/plugins/*/; do
        name="$(basename "$d")"
        ln -sfn "$(cd "$d" && pwd)" "$dir/$name"
    done
    echo "linked bundled plugins under $dir"

# Stdio MCP server (same process the host launches)
run:
    uv run mem0-lite mcp

# Tests. Extra args pass through: just test -k git
test *args:
    uv run pytest {{args}}

# Access-log summary under MEM0_DIR / ~/.mem0
report:
    uv run python scripts/access-report.py

# Wipe local store (~/.mem0 or $MEM0_DIR). Stop MCP hosts first. Confirms.
[confirm("Stop MCP hosts first. Delete ~/.mem0 (or $MEM0_DIR)?")]
wipe:
    #!/usr/bin/env bash
    set -euo pipefail
    dir="${MEM0_DIR:-$HOME/.mem0}"
    rm -rf "$dir"
    echo "wiped $dir"
