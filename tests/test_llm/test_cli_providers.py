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

    def fake_run(
        args: list[str], timeout_s: int = 45, env: dict | None = None
    ) -> tuple[int, str, str]:
        captured["args"] = list(args)
        captured["timeout_s"] = timeout_s
        captured["env"] = env
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


def test_claude_code_extra_args_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_run(monkeypatch, claude_code_mod, (0, "ok", ""))
    provider = ClaudeCodeProvider(extra_args=["--verbose"], env={"MAX_THINKING_TOKENS": "10000"})
    provider.send([Message(role="user", content="hi")])
    assert captured["args"][-1] == "--verbose"
    assert captured["env"] == {"MAX_THINKING_TOKENS": "10000"}


def test_antigravity_extra_args_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_run(monkeypatch, antigravity_mod, (0, "ok", ""))
    AntigravityProvider(extra_args=["--flag"], env={"FOO": "bar"}).send(
        [Message(role="user", content="hi")]
    )
    assert "--flag" in captured["args"]
    assert captured["env"] == {"FOO": "bar"}


def test_codex_extra_args_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Proc:
        returncode = 0
        stdout = ""

    def fake_run(args: list[str], **kwargs: Any) -> _Proc:
        captured["args"] = list(args)
        captured["env"] = kwargs.get("env")
        return _Proc()

    from re_agent.llm import codex_cli as codex_mod

    monkeypatch.setattr(codex_mod.subprocess, "run", fake_run)
    # Avoid touching the real filesystem for the output-last-message read.
    monkeypatch.setattr(codex_mod.Path, "read_text", lambda self, encoding="utf-8": "done")

    provider = codex_mod.CodexCLIProvider(
        extra_args=["-c", "model_reasoning_effort=high"], env={"BAZ": "1"}
    )
    out = provider.send([Message(role="user", content="hi")])
    assert out == "done"
    # extra_args land among the flags, immediately before the positional prompt.
    args = captured["args"]
    assert args[-3:-1] == ["-c", "model_reasoning_effort=high"]
    assert captured["env"] is not None and captured["env"]["BAZ"] == "1"


def test_registry_creates_cli_providers() -> None:
    cc = create_provider(LLMConfig(provider="claude-code"))
    agy = create_provider(LLMConfig(provider="antigravity"))
    assert isinstance(cc, LLMProvider)
    assert isinstance(agy, LLMProvider)
    assert cc.supports_conversations
    assert agy.supports_conversations
