"""Configuration schema dataclasses for re-agent."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProjectProfile:
    """Project-specific patterns and paths."""

    hook_patterns: list[str] = field(default_factory=lambda: [
        r"RH_ScopedInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)",
        r"RH_ScopedVirtualInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)",
    ])
    stub_patterns: list[str] = field(default_factory=lambda: [
        r"plugin::Call",
    ])
    stub_markers: list[str] = field(default_factory=lambda: [
        "NOTSA_UNREACHABLE",
    ])
    stub_call_prefix: str = "plugin::Call"
    class_macro: str = "RH_ScopedClass"
    source_root: str = "source/game_sa"
    leaked_source_root: str = "BrnEntityModuleUnity/BrnEntityModuleUnity-split-v1"
    source_extensions: list[str] = field(default_factory=lambda: [
        ".cpp", ".h", ".hpp",
    ])
    hooks_csv: str | None = "docs/hooks.csv"


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: str = "anthropic"
    model: str = "claude-opus-4-8"
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: int = 1800
    # --- LiteLLM (API tier) tuning, forwarded to litellm.completion() ---------
    # Reasoning effort for o-series / GPT-5 / reasoning models ("minimal" |
    # "low" | "medium" | "high").  LiteLLM also maps it onto other vendors.
    reasoning_effort: str | None = None
    # Anthropic-style extended thinking, e.g.
    # {"type": "enabled", "budget_tokens": 4096}.
    thinking: dict | None = None
    # Escape hatch: any other litellm.completion() kwargs (top_p, seed, stop,
    # presence_penalty, metadata, …).  Merged last, so it can override anything.
    extra_params: dict = field(default_factory=dict)
    # --- provider chaining / CLI overrides ------------------------------------
    # Ordered alternate providers tried, in order, when the primary raises a
    # transient error (rate-limit / 5xx / timeout).  See ``FallbackProvider``.
    fallbacks: list[LLMConfig] = field(default_factory=list)
    # Agentify MCP launch command override.  ``None`` means "use the provider
    # default" (``npx -y @agentify/desktop mcp``).
    command: str | None = None
    # --- CLI provider pass-through (claude-code / codex / antigravity) ---------
    # Extra argv appended to the CLI invocation, e.g. ["-c", "model_reasoning_effort=high"]
    # for codex.  Ignored by API-tier (LiteLLM) providers.
    extra_args: list[str] = field(default_factory=list)
    # Extra environment variables merged over the process env, e.g.
    # {"MAX_THINKING_TOKENS": "10000"} for claude-code.  Ignored by API providers.
    env: dict = field(default_factory=dict)


@dataclass
class BackendConfig:
    """Decompiler backend configuration."""

    type: str = "ghidra-bridge"
    cli_path: str = "ghidra"
    timeout_s: int = 45
    ida_bin: str | None = None


@dataclass
class ParityConfig:
    """Static parity verification settings."""

    enabled: bool = True
    call_count_warn_diff: int = 3
    inline_wrapper_autoskip: bool = False
    semantic_rules_file: str | None = None
    manual_checks_file: str | None = None
    cache_dir: str = ".cache/re-agent-parity"


@dataclass
class OrchestratorConfig:
    """Orchestrator loop settings."""

    max_review_rounds: int = 4
    max_functions_per_class: int = 10
    objective_verifier_enabled: bool = True
    objective_call_count_tolerance: int = 3
    objective_control_flow_tolerance: int = 2


@dataclass
class OutputConfig:
    """Output and reporting settings."""

    report_dir: str = "reports/re-agent"
    log_dir: str = "reports/re-agent/logs"
    session_file: str = "re-agent-progress.json"
    format: str = "json"


@dataclass
class ReAgentConfig:
    """Top-level configuration for the re-agent system."""

    project_profile: ProjectProfile = field(default_factory=ProjectProfile)
    llm: LLMConfig = field(default_factory=LLMConfig)
    # Optional independent LLM for the checker/review agent.  When ``None`` the
    # checker reuses ``llm``.
    checker_llm: LLMConfig | None = None
    backend: BackendConfig = field(default_factory=BackendConfig)
    parity: ParityConfig = field(default_factory=ParityConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @classmethod
    def create_default(cls) -> ReAgentConfig:
        """Create a configuration with all default values."""
        return cls(
            project_profile=ProjectProfile(),
            llm=LLMConfig(),
            backend=BackendConfig(),
            parity=ParityConfig(),
            orchestrator=OrchestratorConfig(),
            output=OutputConfig(),
        )
