"""Tests for config loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from re_agent.config.loader import load_config
from re_agent.config.schema import ReAgentConfig


def test_load_default_config() -> None:
    config = load_config(None)
    assert isinstance(config, ReAgentConfig)
    assert config.llm.provider == "anthropic"
    assert config.backend.type == "ghidra-bridge"
    assert config.orchestrator.max_review_rounds == 4
    assert config.orchestrator.objective_verifier_enabled is True


def test_load_from_yaml(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert config.project_profile.stub_call_prefix == "plugin::Call"
    assert config.llm.model == "claude-opus-4-8"
    assert config.parity.call_count_warn_diff == 3


def test_cli_overrides() -> None:
    config = load_config(None, cli_overrides={"llm.provider": "openai", "orchestrator.max_review_rounds": "6"})
    assert config.llm.provider == "openai"
    assert config.orchestrator.max_review_rounds == 6


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE_AGENT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RE_AGENT_LLM_MODEL", "gpt-5.5")
    config = load_config(None)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-5.5"


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "re-agent.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_fallbacks_built_as_llmconfig(tmp_path: Path) -> None:
    from re_agent.config.schema import LLMConfig

    path = _write_yaml(
        tmp_path,
        """
llm:
  provider: anthropic
  model: claude-opus-4-8
  fallbacks:
    - provider: gemini
      model: gemini-3.1-pro
    - provider: openai
      model: gpt-5.5
""",
    )
    config = load_config(path)
    assert len(config.llm.fallbacks) == 2
    assert all(isinstance(fb, LLMConfig) for fb in config.llm.fallbacks)
    assert config.llm.fallbacks[0].provider == "gemini"
    assert config.llm.fallbacks[1].model == "gpt-5.5"


def test_nested_fallbacks_depth_guard(tmp_path: Path) -> None:
    # 6 levels of nesting exceeds _MAX_FALLBACK_DEPTH (5).
    nested = "llm:\n  provider: a\n  fallbacks:\n"
    indent = "    "
    for i in range(6):
        nested += f"{indent}- provider: p{i}\n{indent}  fallbacks:\n"
        indent += "    "
    path = _write_yaml(tmp_path, nested)
    with pytest.raises(ValueError, match="depth"):
        load_config(path)


def test_checker_llm_built_when_present(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
llm:
  provider: anthropic
checker_llm:
  provider: gemini
  model: gemini-3.1-flash
""",
    )
    config = load_config(path)
    assert config.checker_llm is not None
    assert config.checker_llm.provider == "gemini"
    assert config.checker_llm.model == "gemini-3.1-flash"


def test_checker_llm_none_by_default() -> None:
    assert load_config(None).checker_llm is None


def test_litellm_tuning_fields_parsed(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
llm:
  provider: anthropic
  model: claude-opus-4-8
  reasoning_effort: high
  thinking:
    type: enabled
    budget_tokens: 4096
  extra_params:
    top_p: 0.95
    seed: 7
""",
    )
    config = load_config(path)
    assert config.llm.reasoning_effort == "high"
    assert config.llm.thinking == {"type": "enabled", "budget_tokens": 4096}
    assert config.llm.extra_params == {"top_p": 0.95, "seed": 7}


def test_checker_llm_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE_AGENT_CHECKER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RE_AGENT_CHECKER_LLM_MODEL", "gpt-5.5")
    config = load_config(None)
    assert config.checker_llm is not None
    assert config.checker_llm.provider == "openai"
    assert config.checker_llm.model == "gpt-5.5"
