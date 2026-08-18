"""mem0-lite command-line interface."""

from __future__ import annotations

import argparse

from mem0_lite.mcp import run_mcp


def main() -> None:
    """CLI entry: mem0-lite mcp."""
    parser = argparse.ArgumentParser(prog="mem0-lite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("mcp", help="Run stdio MCP server")
    args = parser.parse_args()
    if args.command == "mcp":
        run_mcp()


if __name__ == "__main__":
    main()
