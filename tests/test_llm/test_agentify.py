"""Tests for AgentifyProvider — MCP tool resolution and prompt dispatch.

The async MCP transport is not exercised against a live server; instead we mock
the resolved session and drive the synchronous surface (tool resolution,
argument mapping, conversational vs one-shot dispatch, text extraction).

Tool/argument names mirror the real ``agentify_query`` schema:
``prompt`` (required), ``model`` (vendor hint), ``key`` (stable tab), and
``promptPrefix`` (system block).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from re_agent.llm.agentify import (
    AgentifyProvider,
    _extract_text,
    _find_tool,
    _first_match,
)
from re_agent.llm.protocol import LLMProvider, Message

_QUERY_PROPS = [
    "model", "tabId", "key", "bundleName", "prompt", "promptPrefix", "attachments",
]


@dataclass
class _FakeTool:
    name: str
    inputSchema: dict[str, Any] = field(default_factory=dict)


@dataclass
class _TextBlock:
    text: str


@dataclass
class _FakeResult:
    content: list[_TextBlock]


class _FakeSession:
    """Minimal stand-in for an MCP ClientSession."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((name, arguments))
        return _FakeResult(content=[_TextBlock(text=f"reply::{arguments.get('prompt', '')[:20]}")])


def _query_tool() -> _FakeTool:
    return _FakeTool(
        name="agentify_query",
        inputSchema={"properties": {p: {} for p in _QUERY_PROPS}, "required": ["prompt"]},
    )


def _wired_provider(model: str | None = "chatgpt") -> AgentifyProvider:
    """Build a provider wired to a fake session, bypassing subprocess startup."""
    p = AgentifyProvider(model=model)
    p._resolve_tools([_query_tool(), _FakeTool(name="agentify_ensure_ready",
                                               inputSchema={"properties": {"key": {}, "model": {}}})])
    p._session = _FakeSession()
    p._ensure_started = lambda: None  # type: ignore[method-assign]
    p._run = lambda coro: asyncio.run(coro)  # type: ignore[method-assign]
    return p


def test_satisfies_protocol() -> None:
    p = AgentifyProvider()
    assert isinstance(p, LLMProvider)
    assert p.supports_conversations is True


def test_default_command_split() -> None:
    assert AgentifyProvider()._command == ["npx", "-y", "@agentify/desktop", "mcp"]


def test_command_override() -> None:
    assert AgentifyProvider(command="my-mcp --flag")._command == ["my-mcp", "--flag"]


def test_resolve_tools_real_schema() -> None:
    p = AgentifyProvider()
    p._resolve_tools([_query_tool(), _FakeTool(name="agentify_ensure_ready")])
    assert p._query_tool == "agentify_query"
    assert p._prompt_arg == "prompt"
    assert p._key_arg == "key"
    assert p._model_arg == "model"
    assert p._prefix_arg == "promptPrefix"
    assert p._ensure_ready_tool == "agentify_ensure_ready"


def test_resolve_tools_no_query_raises() -> None:
    p = AgentifyProvider()
    with pytest.raises(RuntimeError, match="no recognisable query tool"):
        p._resolve_tools([_FakeTool(name="agentify_tabs"), _FakeTool(name="agentify_status")])


def test_conversation_first_turn_sends_system_as_prefix_and_key() -> None:
    p = _wired_provider(model="claude")
    cid = p.new_conversation("SYSTEM PROMPT")
    out = p.resume(cid, "first question")
    session: _FakeSession = p._session  # type: ignore[assignment]

    # ensure_ready called first, then the query.
    assert session.calls[0][0] == "agentify_ensure_ready"
    name, args = session.calls[-1]
    assert name == "agentify_query"
    assert args["prompt"] == "first question"
    assert args["promptPrefix"] == "SYSTEM PROMPT"
    assert args["model"] == "claude"
    assert args["key"].startswith("re-agent-")
    assert out.startswith("reply::")


def test_conversation_reuses_key_and_drops_prefix_after_first_turn() -> None:
    p = _wired_provider()
    cid = p.new_conversation("SYS")
    p.resume(cid, "q1")
    p.resume(cid, "q2")
    session: _FakeSession = p._session  # type: ignore[assignment]

    query_calls = [c for c in session.calls if c[0] == "agentify_query"]
    key1 = query_calls[0][1]["key"]
    key2 = query_calls[1][1]["key"]
    assert key1 == key2  # same stable tab across turns
    assert "promptPrefix" in query_calls[0][1]  # system on first turn
    assert "promptPrefix" not in query_calls[1][1]  # not resent afterwards


def test_send_one_shot_uses_fresh_key_and_renders_history() -> None:
    p = _wired_provider()
    p.send([
        Message(role="system", content="SYS"),
        Message(role="user", content="hello"),
    ])
    session: _FakeSession = p._session  # type: ignore[assignment]
    name, args = session.calls[-1]
    assert name == "agentify_query"
    assert args["promptPrefix"] == "SYS"
    assert "[USER]" in args["prompt"] and "hello" in args["prompt"]
    assert args["key"].startswith("re-agent-oneshot-")


def test_send_omits_model_when_unset() -> None:
    p = _wired_provider(model=None)
    p.send([Message(role="user", content="hi")])
    session: _FakeSession = p._session  # type: ignore[assignment]
    _, args = session.calls[-1]
    assert "model" not in args


def test_first_match() -> None:
    assert _first_match(["foo", "promptPrefix"], ("promptprefix",)) == "promptPrefix"
    assert _first_match(["foo"], ("prompt",)) is None


def test_find_tool() -> None:
    tools = [_FakeTool(name="agentify_status"), _FakeTool(name="agentify_query")]
    assert _find_tool(tools, ("query",)).name == "agentify_query"
    assert _find_tool(tools, ("navigate",)) is None


def test_extract_text_joins_blocks() -> None:
    assert _extract_text(_FakeResult(content=[_TextBlock("a"), _TextBlock("b")])) == "a\nb"
