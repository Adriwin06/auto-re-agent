"""LLM provider factory registry.

Two kinds of providers exist:

* **API providers** are served through LiteLLM.  The ``provider`` config key is a
  LiteLLM vendor name (``anthropic``, ``openai``, ``gemini``, ``ollama``,
  ``mistral``, ``openrouter``, …) and is forwarded as ``custom_llm_provider``;
  ``model`` is the bare model id.  The special key ``litellm`` lets the ``model``
  string carry the full route itself (no ``custom_llm_provider``).
* **CLI providers** (``codex``, ``claude-code``, ``antigravity``) shell out to a
  local binary and never touch LiteLLM — they need no API key.
"""
from __future__ import annotations

from re_agent.config.schema import LLMConfig
from re_agent.llm.protocol import LLMProvider

# CLI / non-LiteLLM providers that bypass LiteLLM entirely.
CLI_PROVIDERS: tuple[str, ...] = ("codex", "claude-code", "antigravity", "agentify")


def _litellm_provider_names() -> set[str]:
    """Return the set of LiteLLM vendor names, or empty if litellm is unavailable."""
    try:
        import litellm
    except ImportError:
        return set()
    return {getattr(p, "value", p) for p in getattr(litellm, "provider_list", [])}


def create_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate an LLM provider from a configuration object.

    If ``config.fallbacks`` is non-empty, the base provider is wrapped in a
    :class:`~re_agent.llm.fallback.FallbackProvider` that fails over to each
    fallback (recursively built) on transient errors.

    Args:
        config: The LLM configuration specifying provider type, model,
            API key, and other parameters.

    Returns:
        An object satisfying the :class:`LLMProvider` protocol.

    Raises:
        ValueError: If ``config.provider`` is neither a CLI provider, the
            ``litellm`` escape hatch, nor a known LiteLLM vendor name.
    """
    base = _create_base_provider(config)

    if config.fallbacks:
        from re_agent.llm.fallback import FallbackProvider

        chain = [base] + [create_provider(fb) for fb in config.fallbacks]
        return FallbackProvider(chain)

    return base


def _create_base_provider(config: LLMConfig) -> LLMProvider:
    """Build the provider named by ``config.provider`` (without fallback wrapping)."""
    provider = config.provider

    if provider == "agentify":
        from re_agent.llm.agentify import AgentifyProvider

        return AgentifyProvider(
            model=config.model or None,
            command=config.command,
            timeout_s=config.timeout_s,
            env=config.env,
        )

    if provider == "claude-code":
        from re_agent.llm.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider(
            model=config.model or None,
            timeout_s=config.timeout_s,
            extra_args=config.extra_args,
            env=config.env,
        )

    if provider == "antigravity":
        from re_agent.llm.antigravity import AntigravityProvider

        return AntigravityProvider(
            model=config.model or None,
            timeout_s=config.timeout_s,
            extra_args=config.extra_args,
            env=config.env,
        )

    if provider == "codex":
        from re_agent.llm.codex_cli import CodexCLIProvider

        return CodexCLIProvider(
            model=config.model or "gpt-5.5",
            timeout_s=config.timeout_s,
            extra_args=config.extra_args,
            env=config.env,
        )

    # ------------------------------------------------------------------ #
    # Everything else is served through LiteLLM.
    # ------------------------------------------------------------------ #
    from re_agent.llm.litellm_provider import LiteLLMProvider

    # ``litellm`` is an explicit escape hatch: let the model string carry the
    # route and don't pin a vendor.
    custom_llm_provider: str | None = None if provider == "litellm" else provider

    if custom_llm_provider is not None:
        known = _litellm_provider_names()
        if known and custom_llm_provider not in known:
            raise ValueError(
                f"Unknown LLM provider: {provider!r}. Expected a CLI provider "
                f"({', '.join(CLI_PROVIDERS)}), 'litellm', or a LiteLLM vendor name "
                f"(e.g. 'anthropic', 'openai', 'gemini', 'ollama', 'mistral', "
                f"'openrouter'; see litellm.provider_list for the full set)."
            )

    return LiteLLMProvider(
        api_key=config.api_key,
        # API tier needs a concrete id; fall back to the historical default when
        # the config leaves `model` unset (None).
        model=config.model or "claude-opus-4-8",
        custom_llm_provider=custom_llm_provider,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        base_url=config.base_url,
        reasoning_effort=config.reasoning_effort,
        thinking=config.thinking,
        extra_params=config.extra_params,
    )
