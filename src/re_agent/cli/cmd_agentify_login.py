"""re-agent agentify-login — pre-warm Agentify Desktop browser sessions.

Runs the one-time interactive sign-in for every ``agentify`` provider referenced
by the config (``llm``, ``checker_llm``, and any ``fallbacks``), so a later
``re-agent reverse`` run never blocks mid-way waiting for a browser login.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from re_agent.config.loader import load_config
from re_agent.config.schema import LLMConfig, ReAgentConfig


def _collect_agentify_configs(config: ReAgentConfig) -> list[LLMConfig]:
    """Gather every agentify LLMConfig reachable from llm/checker_llm + fallbacks."""
    found: list[LLMConfig] = []

    def walk(llm: LLMConfig | None) -> None:
        if llm is None:
            return
        if llm.provider == "agentify":
            found.append(llm)
        for fb in llm.fallbacks:
            walk(fb)

    walk(config.llm)
    walk(config.checker_llm)
    return found


def cmd_agentify_login(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    configs = _collect_agentify_configs(config)

    if not configs:
        print(
            "No 'agentify' provider found in llm/checker_llm/fallbacks. "
            "Set `provider: agentify` in re-agent.yaml first.",
            file=sys.stderr,
        )
        return 1

    from re_agent.llm.agentify import AgentifyProvider

    # Login is per-vendor (cached in Agentify's browser profile), so dedupe by
    # (vendor, launch-command) to avoid signing into the same UI twice.
    seen: set[tuple[str | None, str | None]] = set()
    for c in configs:
        vendor = c.model or "(default)"
        sig = (c.model, c.command)
        if sig in seen:
            continue
        seen.add(sig)

        print(
            f"Warming Agentify session for '{vendor}' — a browser window will open; "
            "sign in there if prompted...",
            file=sys.stderr,
        )
        provider = AgentifyProvider(model=c.model or None, command=c.command, timeout_s=c.timeout_s)
        try:
            reply = provider.warm_up()
        except Exception as exc:  # noqa: BLE001
            print(f"  -> FAILED for '{vendor}': {exc}", file=sys.stderr)
            return 1
        finally:
            provider.close()
        print(f"  -> ready: '{vendor}' responded: {reply[:80]!r}")

    print("\nAgentify login complete. `re-agent reverse` runs will no longer block on login.")
    return 0
