"""LLM provider factory registry.

API providers are consolidated behind :class:`LiteLLMProvider`; the legacy
``claude`` / ``openai`` / ``openai-compat`` keys are kept as aliases that route
through LiteLLM with appropriate model-string normalization.  CLI providers
(``claude-code``, ``antigravity``, ``codex``) shell out to local binaries and
need no API key.
"""
from __future__ import annotations

from re_agent.config.schema import LLMConfig
from re_agent.llm.protocol import LLMProvider


def _normalize_model(provider: str, model: str) -> str:
    """Map a legacy provider key + model onto a LiteLLM model string."""
    if provider == "claude":
        # Bare ``claude-*`` ids route to Anthropic via the ``anthropic/`` prefix.
        if model.startswith("claude-"):
            return f"anthropic/{model}"
        return model
    if provider == "openai-compat":
        # Custom OpenAI-compatible endpoints use the ``openai/`` prefix + api_base.
        if "/" not in model:
            return f"openai/{model}"
        return model
    # "openai" and "litellm": pass through verbatim.
    return model


def create_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate an LLM provider from a configuration object.

    Args:
        config: The LLM configuration specifying provider type, model,
            API key, and other parameters.

    Returns:
        An object satisfying the :class:`LLMProvider` protocol.

    Raises:
        ValueError: If ``config.provider`` is not a recognised provider name.
    """
    if config.provider in ("litellm", "claude", "openai", "openai-compat"):
        from re_agent.llm.litellm_provider import LiteLLMProvider

        return LiteLLMProvider(
            api_key=config.api_key,
            model=_normalize_model(config.provider, config.model),
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            base_url=config.base_url,
        )

    if config.provider == "claude-code":
        from re_agent.llm.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider(
            model=config.model or None,
            timeout_s=config.timeout_s,
        )

    if config.provider == "antigravity":
        from re_agent.llm.antigravity import AntigravityProvider

        return AntigravityProvider(
            timeout_s=config.timeout_s,
        )

    if config.provider == "codex":
        from re_agent.llm.codex_cli import CodexCLIProvider

        return CodexCLIProvider(
            model=config.model or "gpt-5.4",
            timeout_s=config.timeout_s,
        )

    raise ValueError(
        f"Unknown LLM provider: {config.provider!r}. "
        f"Supported providers: 'litellm', 'claude', 'openai', 'openai-compat', "
        f"'claude-code', 'antigravity', 'codex'."
    )
