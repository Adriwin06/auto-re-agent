"""CLI entry point for re-agent."""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="re-agent",
        description="Autonomous reverse engineering agent",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("--config", default="re-agent.yaml", help="Config file path")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_p = sub.add_parser("init", help="Initialize re-agent.yaml config file")
    init_p.add_argument("--profile", default=None, help="Use a built-in project profile template")

    # reverse
    rev_p = sub.add_parser("reverse", help="Reverse engineer functions")
    rev_p.add_argument("--address", help="Single function address to reverse")
    rev_p.add_argument("--class", dest="class_name", help="Class name for class-level reversal")
    rev_p.add_argument("--max-functions", type=int, default=None, help="Max functions per class")
    rev_p.add_argument("--max-rounds", type=int, default=None, help="Max review rounds per function")
    rev_p.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    rev_p.add_argument("--skip-parity", action="store_true", help="Skip parity check after PASS")

    # parity
    par_p = sub.add_parser("parity", help="Run parity checks on hooked functions")
    par_p.add_argument("--address", action="append", help="Specific address (repeatable)")
    par_p.add_argument("--filter", help="Regex filter on symbol/class")
    par_p.add_argument("--limit", type=int, help="Max functions to check")
    par_p.add_argument("--skip-ghidra", action="store_true", help="Source-only checks")
    par_p.add_argument("--strict-exit", action="store_true", help="Exit 1 on RED")
    par_p.add_argument("--output", help="Output JSON report path")

    # status
    stat_p = sub.add_parser("status", help="Show reversal progress")
    stat_p.add_argument("--class", dest="class_name", help="Filter by class")
    stat_p.add_argument("--format", choices=["text", "json", "markdown"], default="text")

    # web
    web_p = sub.add_parser("web", help="Start the local live web UI")
    web_p.add_argument("--host", default="127.0.0.1", help="Bind host")
    web_p.add_argument("--port", type=int, default=8787, help="Bind port")
    web_p.add_argument("--open", action="store_true", help="Open the UI in the default browser")

    # agentify-login
    sub.add_parser(
        "agentify-login",
        help="Pre-warm Agentify Desktop browser sessions (one-time interactive sign-in)",
    )

    # prompt
    prompt_p = sub.add_parser("prompt", help="Display the prompt that would be sent for a function")
    prompt_p.add_argument("--address", required=True, help="Function address to display prompt for")
    prompt_p.add_argument("--class", dest="class_name", help="Optional class name override")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "init":
        from re_agent.cli.cmd_init import cmd_init
        return cmd_init(args)

    if args.command == "reverse":
        from re_agent.cli.cmd_reverse import cmd_reverse
        return cmd_reverse(args)

    if args.command == "parity":
        from re_agent.cli.cmd_parity import cmd_parity
        return cmd_parity(args)

    if args.command == "status":
        from re_agent.cli.cmd_status import cmd_status
        return cmd_status(args)

    if args.command == "web":
        from re_agent.cli.cmd_web import cmd_web
        return cmd_web(args)

    if args.command == "agentify-login":
        from re_agent.cli.cmd_agentify_login import cmd_agentify_login
        return cmd_agentify_login(args)

    if args.command == "prompt":
        from re_agent.cli.cmd_prompt import cmd_prompt
        return cmd_prompt(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
