"""Tests for the LiteLLM-backed provider and its registry wiring."""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from re_agent.config.schema import LLMConfig
from re_agent.llm.litellm_provider import LiteLLMProvider
from re_agent.llm.protocol import LLMProvider, Message
from re_agent.llm.registry import create_provider


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a fake ``litellm`` module and capture completion kwargs."""
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> _FakeResponse:
        captured.update(kwargs)
        return _FakeResponse("hello from litellm")

    module = types.ModuleType("litellm")
    module.completion = fake_completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", module)
    return captured


def test_send_returns_text_and_passes_system(fake_litellm: dict[str, Any]) -> None:
    provider = LiteLLMProvider(model="gpt-5.5", max_tokens=128, temperature=0.0)
    out = provider.send(
        [
            Message(role="system", content="be terse"),
            Message(role="user", content="hi"),
        ]
    )
    assert out == "hello from litellm"
    assert fake_litellm["model"] == "gpt-5.5"
    assert fake_litellm["max_tokens"] == 128
    # System message is forwarded verbatim as a role:system message.
    assert fake_litellm["messages"][0] == {"role": "system", "content": "be terse"}
    assert fake_litellm["messages"][1] == {"role": "user", "content": "hi"}


def test_base_url_maps_to_api_base(fake_litellm: dict[str, Any]) -> None:
    provider = LiteLLMProvider(model="openai/local", base_url="http://localhost:8000/v1")
    provider.send([Message(role="user", content="ping")])
    assert fake_litellm["api_base"] == "http://localhost:8000/v1"


def test_kwargs_override_model(fake_litellm: dict[str, Any]) -> None:
    provider = LiteLLMProvider(model="gpt-5.5")
    provider.send([Message(role="user", content="ping")], model="claude-opus-4-8")
    assert fake_litellm["model"] == "claude-opus-4-8"


def test_custom_llm_provider_forwarded(fake_litellm: dict[str, Any]) -> None:
    provider = LiteLLMProvider(model="claude-opus-4-8", custom_llm_provider="anthropic")
    provider.send([Message(role="user", content="hi")])
    assert fake_litellm["model"] == "claude-opus-4-8"
    assert fake_litellm["custom_llm_provider"] == "anthropic"


def test_custom_llm_provider_omitted_when_none(fake_litellm: dict[str, Any]) -> None:
    provider = LiteLLMProvider(model="openrouter/anthropic/claude-opus-4-8")
    provider.send([Message(role="user", content="hi")])
    assert "custom_llm_provider" not in fake_litellm


def test_registry_vendor_sets_custom_llm_provider() -> None:
    provider = create_provider(LLMConfig(provider="anthropic", model="claude-opus-4-8"))
    assert isinstance(provider, LLMProvider)
    assert isinstance(provider, LiteLLMProvider)
    assert provider._custom_llm_provider == "anthropic"
    assert provider._model == "claude-opus-4-8"  # bare id, no munging


def test_registry_openai_with_base_url() -> None:
    provider = create_provider(
        LLMConfig(provider="openai", model="my-model", base_url="http://x/v1")
    )
    assert isinstance(provider, LiteLLMProvider)
    assert provider._custom_llm_provider == "openai"
    assert provider._base_url == "http://x/v1"


def test_registry_litellm_escape_hatch_has_no_custom_provider() -> None:
    provider = create_provider(
        LLMConfig(provider="litellm", model="openrouter/anthropic/claude-opus-4-8")
    )
    assert isinstance(provider, LiteLLMProvider)
    assert provider._custom_llm_provider is None
    assert provider.supports_conversations


def test_registry_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_provider(LLMConfig(provider="anthropci"))
