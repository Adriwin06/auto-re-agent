"""Tests for the `agentify-login` CLI command."""
from __future__ import annotations

from pathlib import Path

import pytest

from re_agent.cli.cmd_agentify_login import _collect_agentify_configs, cmd_agentify_login
from re_agent.cli.main import main
from re_agent.config.schema import LLMConfig, ReAgentConfig


def test_collect_finds_agentify_in_llm_checker_and_fallbacks() -> None:
    config = ReAgentConfig(
        llm=LLMConfig(
            provider="anthropic",
            fallbacks=[LLMConfig(provider="agentify", model="chatgpt")],
        ),
        checker_llm=LLMConfig(provider="agentify", model="claude"),
    )
    found = _collect_agentify_configs(config)
    vendors = {c.model for c in found}
    assert vendors == {"chatgpt", "claude"}


def test_collect_empty_when_no_agentify() -> None:
    config = ReAgentConfig(llm=LLMConfig(provider="anthropic"))
    assert _collect_agentify_configs(config) == []


def test_login_no_agentify_provider_returns_1(tmp_path: Path) -> None:
    config_path = tmp_path / "re-agent.yaml"
    config_path.write_text("llm:\n  provider: anthropic\n")
    assert main(["--config", str(config_path), "agentify-login"]) == 1


def test_login_warms_each_vendor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    warmed: list[str | None] = []

    class _FakeProvider:
        def __init__(self, model: str | None = None, **_: object) -> None:
            self.model = model

        def warm_up(self) -> str:
            warmed.append(self.model)
            return "ready"

        def close(self) -> None:
            pass

    import re_agent.llm.agentify as agentify_mod

    monkeypatch.setattr(agentify_mod, "AgentifyProvider", _FakeProvider)

    config_path = tmp_path / "re-agent.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: agentify\n"
        "  model: chatgpt\n"
        "checker_llm:\n"
        "  provider: agentify\n"
        "  model: claude\n"
    )
    import argparse

    rc = cmd_agentify_login(argparse.Namespace(config=str(config_path)))
    assert rc == 0
    assert set(warmed) == {"chatgpt", "claude"}


def test_login_dedupes_same_vendor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str | None] = []

    class _FakeProvider:
        def __init__(self, model: str | None = None, **_: object) -> None:
            self.model = model

        def warm_up(self) -> str:
            calls.append(self.model)
            return "ready"

        def close(self) -> None:
            pass

    import re_agent.llm.agentify as agentify_mod

    monkeypatch.setattr(agentify_mod, "AgentifyProvider", _FakeProvider)

    config_path = tmp_path / "re-agent.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: agentify\n"
        "  model: chatgpt\n"
        "  fallbacks:\n"
        "    - provider: agentify\n"
        "      model: chatgpt\n"  # same vendor -> deduped
    )
    import argparse

    cmd_agentify_login(argparse.Namespace(config=str(config_path)))
    assert calls == ["chatgpt"]  # only warmed once
