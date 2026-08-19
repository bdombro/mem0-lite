"""mem0-lite command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mem0_lite.access_report import build_report
from mem0_lite.logs import get_by_ts, get_column


def _print_json(obj: Any) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def _dispatch_log(args: argparse.Namespace) -> int:
    try:
        if args.log_command == "report":
            return _print_json(build_report())
        if args.log_command == "getByTs":
            return _print_json(get_by_ts(args.ts))
        if args.log_command == "getColumn":
            print(get_column(args.column))
            return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry: mem0-lite mcp | log …"""
    parser = argparse.ArgumentParser(prog="mem0-lite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("mcp", help="Run stdio MCP server")

    log = subparsers.add_parser("log", help="Query local JSONL logs (no Qdrant)")
    log_sub = log.add_subparsers(dest="log_command", required=True)
    log_sub.add_parser("report", help="Summarize access + feedback as JSON")

    get_ts = log_sub.add_parser(
        "getByTs",
        help="Aggregate access, feedback, and debug rows for one timestamp",
    )
    get_ts.add_argument("ts", help="ISO timestamp (tool-call ts, or feedback ts / call_ts)")

    get_col = log_sub.add_parser(
        "getColumn",
        help="Comma-separated values of one column across all logs",
    )
    get_col.add_argument(
        "column",
        help="Top-level key or dotted path (tool, params.query, request.filters.project)",
    )

    args = parser.parse_args(argv)
    if args.command == "mcp":
        from mem0_lite.mcp import run_mcp

        run_mcp()
        return 0
    if args.command == "log":
        return _dispatch_log(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
