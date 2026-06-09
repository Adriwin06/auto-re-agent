"""Tests for FallbackProvider — transient failover across a provider chain."""
from __future__ import annotations

import pytest

from re_agent.llm.fallback import FallbackProvider, _is_transient
from re_agent.llm.protocol import LLMProvider, Message


class _FakeProvider:
    """Records calls; optionally raises a fixed exception on send()."""

    def __init__(self, reply: str, raises: BaseException | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[list[Message]] = []

    def send(self, messages: list[Message], **kwargs: object) -> str:
        self.calls.append(list(messages))
        if self.raises is not None:
            raise self.raises
        return self.reply

    @property
    def supports_conversations(self) -> bool:
        return False

    def new_conversation(self, system: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def resume(self, conversation_id: str, message: str) -> str:  # pragma: no cover
        raise NotImplementedError


def test_fallbackprovider_satisfies_protocol() -> None:
    fp = FallbackProvider([_FakeProvider("a")])
    assert isinstance(fp, LLMProvider)
    assert fp.supports_conversations is True


def test_empty_chain_rejected() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        FallbackProvider([])


def test_primary_used_when_healthy() -> None:
    primary = _FakeProvider("primary")
    secondary = _FakeProvider("secondary")
    fp = FallbackProvider([primary, secondary])
    assert fp.send([Message(role="user", content="hi")]) == "primary"
    assert len(secondary.calls) == 0


def test_failover_on_transient_error() -> None:
    primary = _FakeProvider("primary", raises=TimeoutError("rate limit hit"))
    secondary = _FakeProvider("secondary")
    fp = FallbackProvider([primary, secondary])
    assert fp.send([Message(role="user", content="hi")]) == "secondary"
    assert len(primary.calls) == 1
    assert len(secondary.calls) == 1


def test_non_transient_error_propagates_without_failover() -> None:
    primary = _FakeProvider("primary", raises=ValueError("bad auth"))
    secondary = _FakeProvider("secondary")
    fp = FallbackProvider([primary, secondary])
    with pytest.raises(ValueError, match="bad auth"):
        fp.send([Message(role="user", content="hi")])
    assert len(secondary.calls) == 0


def test_last_provider_error_reraised() -> None:
    primary = _FakeProvider("primary", raises=TimeoutError("timeout"))
    secondary = _FakeProvider("secondary", raises=ConnectionError("down"))
    fp = FallbackProvider([primary, secondary])
    with pytest.raises(ConnectionError):
        fp.send([Message(role="user", content="hi")])


def test_resume_replays_full_history_to_fallback() -> None:
    primary = _FakeProvider("x", raises=ConnectionError("connection reset"))
    secondary = _FakeProvider("recovered")
    fp = FallbackProvider([primary, secondary])

    cid = fp.new_conversation("SYSTEM")
    out = fp.resume(cid, "first question")
    assert out == "recovered"

    # The secondary received the full history: system + user.
    sent = secondary.calls[-1]
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[0].content == "SYSTEM"
    assert sent[-1].content == "first question"


def test_index_resets_per_conversation() -> None:
    # Primary recovers (no longer raising) on the second turn -> should be tried
    # again because the index is local to each send() call.
    class _FlakyPrimary(_FakeProvider):
        def __init__(self) -> None:
            super().__init__("primary-ok")
            self._first = True

        def send(self, messages: list[Message], **kwargs: object) -> str:
            self.calls.append(list(messages))
            if self._first:
                self._first = False
                raise TimeoutError("temporarily unavailable")
            return self.reply

    primary = _FlakyPrimary()
    secondary = _FakeProvider("secondary")
    fp = FallbackProvider([primary, secondary])
    cid = fp.new_conversation("S")
    assert fp.resume(cid, "q1") == "secondary"  # primary fails -> secondary
    assert fp.resume(cid, "q2") == "primary-ok"  # primary retried from index 0


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("x"),
        ConnectionError("x"),
        RuntimeError("HTTP 503 service unavailable"),
        RuntimeError("agy -p failed: 429 rate limit"),
    ],
)
def test_is_transient_true(exc: BaseException) -> None:
    assert _is_transient(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("nope"),
        RuntimeError("invalid api key"),
        KeyError("missing"),
    ],
)
def test_is_transient_false(exc: BaseException) -> None:
    assert _is_transient(exc) is False


def test_registry_wraps_when_fallbacks_present() -> None:
    from re_agent.config.schema import LLMConfig
    from re_agent.llm.registry import create_provider

    config = LLMConfig(
        provider="claude-code",
        fallbacks=[LLMConfig(provider="antigravity")],
    )
    provider = create_provider(config)
    assert isinstance(provider, FallbackProvider)
    assert len(provider._providers) == 2


def test_registry_no_wrap_without_fallbacks() -> None:
    from re_agent.config.schema import LLMConfig
    from re_agent.llm.registry import create_provider

    provider = create_provider(LLMConfig(provider="claude-code"))
    assert not isinstance(provider, FallbackProvider)
