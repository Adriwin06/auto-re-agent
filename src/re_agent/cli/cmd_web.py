"""re-agent web command — local live observer and launcher."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_web(args: argparse.Namespace) -> int:
    """Start the local web UI server."""
    from re_agent.web.app import serve

    try:
        return serve(
            config_path=Path(args.config),
            host=args.host,
            port=args.port,
            open_browser=args.open,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
