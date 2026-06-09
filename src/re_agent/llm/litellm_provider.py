"""LiteLLM-backed LLM provider.

A single provider that wraps :func:`litellm.completion`, so any backend LiteLLM
supports (Anthropic, OpenAI, Gemini, OpenRouter, OpenAI-compatible endpoints, …)
can be reached through one code path.  The ``model`` string selects the backend,
e.g. ``"claude-opus-4-6"``, ``"gemini/gemini-2.5-pro"``,
``"openrouter/anthropic/claude-opus-4"``, ``"gpt-4o"``.
"""
from __future__ import annotations

import uuid
from typing import Any

from re_agent.llm.protocol import Message


class LiteLLMProvider:
    """LLM provider backed by ``litellm.completion``.

    Implements :class:`LLMProvider`.  Authentication is handled by LiteLLM from
    the standard provider env vars (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
    ``GEMINI_API_KEY``, ``OPENROUTER_API_KEY``, …); ``api_key`` may also be passed
    explicitly to override them.

    Args:
        api_key: Optional API key forwarded to LiteLLM.  If ``None``, LiteLLM
            resolves credentials from the appropriate environment variable.
        model: LiteLLM model string selecting the backend.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature (``0.0`` = deterministic).
        base_url: Optional API base URL for OpenAI-compatible / self-hosted endpoints.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-opus-4-6",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._base_url = base_url
        self._conversations: dict[str, list[Message]] = {}

    # -- LLMProvider interface ------------------------------------------------

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        """Send messages via LiteLLM and return the assistant response text."""
        try:
            import litellm
        except ImportError as err:
            raise ImportError(
                "litellm is required for the 'litellm' provider. "
                "Install it with: pip install litellm"
            ) from err

        api_messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        completion_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": api_messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
        }
        if self._api_key is not None:
            completion_kwargs["api_key"] = self._api_key
        if self._base_url is not None:
            completion_kwargs["api_base"] = self._base_url

        response = litellm.completion(**completion_kwargs)

        return response.choices[0].message.content or ""

    @property
    def supports_conversations(self) -> bool:
        """LiteLLM providers support multi-turn conversations (client-side history)."""
        return True

    def new_conversation(self, system: str) -> str:
        """Create a new conversation with a system prompt, returning its ID."""
        cid = uuid.uuid4().hex
        self._conversations[cid] = [Message(role="system", content=system)]
        return cid

    def resume(self, conversation_id: str, message: str) -> str:
        """Append a user message to the conversation and return the response."""
        history = self._conversations.get(conversation_id)
        if history is None:
            raise KeyError(f"Unknown conversation ID: {conversation_id}")

        history.append(Message(role="user", content=message))
        response_text = self.send(list(history))
        history.append(Message(role="assistant", content=response_text))
        return response_text
