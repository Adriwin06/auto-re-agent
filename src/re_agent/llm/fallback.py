"""Fallback LLM provider — fail over to alternate providers on transient errors.

Wraps a primary provider plus an ordered chain of fallbacks.  When the active
provider raises a *transient* error (rate-limit, 5xx, timeout, connection drop)
the wrapper advances to the next provider and retries with the **same full
message history**, so a mid-conversation vendor switch loses no context.

The wrapper **owns** the conversation store and reports
``supports_conversations = True``.  This is deliberate: agents like the reverser
keep no message history of their own — their stateless fix path would drop the
system prompt and decompile context.  By driving every turn through
``send(full_history)`` on whichever provider is currently healthy, we get
stateless vendor-switching *and* full context on every turn.

The provider index is local to each :meth:`send` call, so every new conversation
(i.e. every new function target) naturally restarts from the primary.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from re_agent.llm.protocol import LLMProvider, Message

logger = logging.getLogger(__name__)

# Substrings that mark a CLI-provider RuntimeError (wrapped stderr) as transient.
# Kept small and explicit; anything not matching fails fast (e.g. auth errors).
_TRANSIENT_CLI_SIGNATURES: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "timeout",
    "timed out",
    "503",
    "502",
    "500",
    "overloaded",
    "service unavailable",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "empty response",
)


def _is_transient(exc: BaseException) -> bool:
    """Return True if ``exc`` looks like a retryable, provider-agnostic failure."""
    # Stdlib transient signals (CLI subprocess timeouts, socket drops).
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # LiteLLM exception hierarchy — imported lazily so CLI-only setups that never
    # install litellm still work.
    try:
        from litellm import exceptions as litellm_exc  # type: ignore[import-untyped]
    except ImportError:
        litellm_exc = None  # type: ignore[assignment]

    if litellm_exc is not None:
        transient_types = tuple(
            t
            for t in (
                getattr(litellm_exc, "RateLimitError", None),
                getattr(litellm_exc, "ServiceUnavailableError", None),
                getattr(litellm_exc, "APIConnectionError", None),
                getattr(litellm_exc, "Timeout", None),
                getattr(litellm_exc, "APITimeoutError", None),
                getattr(litellm_exc, "InternalServerError", None),
            )
            if isinstance(t, type)
        )
        if transient_types and isinstance(exc, transient_types):
            return True

    # CLI providers raise RuntimeError wrapping the process stderr.  Match a small
    # set of known transient signatures; everything else fails fast.
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        return any(sig in msg for sig in _TRANSIENT_CLI_SIGNATURES)

    return False


class FallbackProvider:
    """LLM provider that fails over across a chain on transient errors.

    Args:
        providers: ``[primary, *fallbacks]`` — already-instantiated providers,
            tried in order.  Must be non-empty.
    """

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = providers
        self._conversations: dict[str, list[Message]] = {}

    # -- LLMProvider interface ------------------------------------------------

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        """Try each provider in order; fail over on transient errors only."""
        last_exc: BaseException | None = None
        for idx, provider in enumerate(self._providers):
            try:
                return provider.send(messages, **kwargs)
            except Exception as exc:  # noqa: BLE001 — classify, then re-raise
                last_exc = exc
                is_last = idx == len(self._providers) - 1
                if is_last or not _is_transient(exc):
                    raise
                logger.warning(
                    "LLM provider #%d (%s) failed transiently (%s); "
                    "failing over to #%d",
                    idx,
                    type(provider).__name__,
                    exc,
                    idx + 1,
                )
        # Unreachable (loop either returns or raises), but keeps type-checkers happy.
        assert last_exc is not None
        raise last_exc

    @property
    def supports_conversations(self) -> bool:
        """The wrapper owns history so it can replay across providers."""
        return True

    def new_conversation(self, system: str) -> str:
        """Create a new conversation with a system prompt, returning its ID."""
        cid = uuid.uuid4().hex
        self._conversations[cid] = [Message(role="system", content=system)]
        return cid

    def resume(self, conversation_id: str, message: str) -> str:
        """Append a user message and send the full history to a healthy provider."""
        history = self._conversations.get(conversation_id)
        if history is None:
            raise KeyError(f"Unknown conversation ID: {conversation_id}")

        history.append(Message(role="user", content=message))
        response_text = self.send(list(history))
        history.append(Message(role="assistant", content=response_text))
        return response_text
