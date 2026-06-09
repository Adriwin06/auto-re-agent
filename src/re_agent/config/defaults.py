"""Default configuration templates for re-agent."""
from __future__ import annotations

from typing import Any

DEFAULT_CONFIG_YAML: str = """\
# re-agent configuration
# See: https://github.com/dryxio/auto-re-agent for documentation.

project_profile:
  hook_patterns:
    - "RH_ScopedInstall\\\\s*\\\\(\\\\s*(\\\\w+)\\\\s*,\\\\s*(0x[0-9A-Fa-f]+)"
    - "RH_ScopedVirtualInstall\\\\s*\\\\(\\\\s*(\\\\w+)\\\\s*,\\\\s*(0x[0-9A-Fa-f]+)"
  stub_patterns:
    - "plugin::Call"
  stub_markers:
    - "NOTSA_UNREACHABLE"
  stub_call_prefix: "plugin::Call"
  class_macro: "RH_ScopedClass"
  source_root: "source/game_sa"
  source_extensions:
    - ".cpp"
    - ".h"
    - ".hpp"
  hooks_csv: "docs/hooks.csv"

llm:
  # API providers are served through LiteLLM. The model string selects the
  # backend; auth is read from the matching env var (ANTHROPIC_API_KEY,
  # OPENAI_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, ...).
  provider: "litellm"
  model: "claude-opus-4-8"   # or "gpt-5.5", "gemini/gemini-3.1-pro", "openrouter/anthropic/claude-opus-4-8"
  # Legacy aliases (route through LiteLLM): "claude", "openai", "openai-compat".
  # CLI providers (no API key needed):
  #   provider: "claude-code"   # uses ~/.claude/ auth; model -> --model
  #   provider: "antigravity"   # uses Google Sign-In; requires `agy` on PATH
  #   provider: "codex"         # uses ChatGPT login
  # api_key: null  # Set via RE_AGENT_LLM_API_KEY env var
  # base_url: null  # Set via RE_AGENT_LLM_BASE_URL env var (api_base for OpenAI-compatible endpoints)
  max_tokens: 4096
  temperature: 0.0
  timeout_s: 1800

backend:
  type: "ghidra-bridge"
  cli_path: "ghidra"
  timeout_s: 45

parity:
  enabled: true
  call_count_warn_diff: 3
  inline_wrapper_autoskip: false
  # semantic_rules_file: null
  # manual_checks_file: null
  cache_dir: ".cache/re-agent-parity"

orchestrator:
  max_review_rounds: 4
  max_functions_per_class: 10
  objective_verifier_enabled: true
  objective_call_count_tolerance: 3
  objective_control_flow_tolerance: 2

output:
  report_dir: "reports/re-agent"
  log_dir: "reports/re-agent/logs"
  session_file: "re-agent-progress.json"
  format: "json"
"""

EXAMPLE_PROFILE_TEMPLATES: dict[str, dict[str, Any]] = {
    "gta-reversed": {
        "hook_patterns": [
            r"RH_ScopedInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)",
            r"RH_ScopedVirtualInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)",
        ],
        "stub_patterns": [
            r"plugin::Call",
        ],
        "stub_markers": [
            "NOTSA_UNREACHABLE",
        ],
        "stub_call_prefix": "plugin::Call",
        "class_macro": "RH_ScopedClass",
        "source_root": "source/game_sa",
        "source_extensions": [".cpp", ".h", ".hpp"],
        "hooks_csv": "docs/hooks.csv",
    },
    "openrct2": {
        "hook_patterns": [
            r"HOOK_FUNCTION\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)",
        ],
        "stub_patterns": [
            r"original_function\(",
        ],
        "stub_markers": [
            "NOT_IMPLEMENTED",
        ],
        "stub_call_prefix": "original_function",
        "class_macro": "",
        "source_root": "src",
        "source_extensions": [".cpp", ".h", ".hpp"],
        "hooks_csv": None,
    },
}
