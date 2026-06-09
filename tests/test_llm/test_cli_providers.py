"""Tests for the CLI-backed providers (claude-code, antigravity)."""
from __future__ import annotations

from typing import Any

import pytest

from re_agent.config.schema import LLMConfig
from re_agent.llm import antigravity as antigravity_mod
from re_agent.llm import claude_code as claude_code_mod
from re_agent.llm.antigravity import AntigravityProvider
from re_agent.llm.claude_code import ClaudeCodeProvider
from re_agent.llm.protocol import LLMProvider, Message
from re_agent.llm.registry import create_provider


def _patch_run(monkeypatch: pytest.MonkeyPatch, module: Any, result: tuple[int, str, str]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], timeout_s: int = 45) -> tuple[int, str, str]:
        captured["args"] = list(args)
        captured["timeout_s"] = timeout_s
        return result

    monkeypatch.setattr(module, "run_cmd_split", fake_run)
    return captured


def test_claude_code_argv_and_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_run(monkeypatch, claude_code_mod, (0, "the answer", ""))
    provider = ClaudeCodeProvider(model="claude-opus-4-8", timeout_s=120)
    out = provider.send([Message(role="user", content="hi")])
    assert out == "the answer"
    assert captured["args"][:2] == ["claude", "--print"]
    assert "--model" in captured["args"]
    assert captured["args"][captured["args"].index("--model") + 1] == "claude-opus-4-8"
    assert captured["timeout_s"] == 120


def test_claude_code_no_model_omits_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_run(monkeypatch, claude_code_mod, (0, "ok", ""))
    ClaudeCodeProvider().send([Message(role="user", content="hi")])
    assert "--model" not in captured["args"]


def test_claude_code_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, claude_code_mod, (1, "", "boom"))
    with pytest.raises(RuntimeError, match="claude --print failed"):
        ClaudeCodeProvider().send([Message(role="user", content="hi")])


def test_antigravity_argv_and_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_run(monkeypatch, antigravity_mod, (0, "agy reply", ""))
    out = AntigravityProvider(timeout_s=99).send([Message(role="user", content="hi")])
    assert out == "agy reply"
    assert captured["args"][:2] == ["agy", "-p"]
    assert captured["timeout_s"] == 99


def test_antigravity_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, antigravity_mod, (2, "", "nope"))
    with pytest.raises(RuntimeError, match="agy -p failed"):
        AntigravityProvider().send([Message(role="user", content="hi")])


def test_registry_creates_cli_providers() -> None:
    cc = create_provider(LLMConfig(provider="claude-code"))
    agy = create_provider(LLMConfig(provider="antigravity"))
    assert isinstance(cc, LLMProvider)
    assert isinstance(agy, LLMProvider)
    assert cc.supports_conversations
    assert agy.supports_conversations
