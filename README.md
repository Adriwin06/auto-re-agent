# re-agent

Autonomous reverse-engineering agent — source-aware reverser/checker loop, objective verifier, parity engine, and Ghidra backend.

## Overview

Demo: [YouTube](https://youtu.be/zBQJYMKmwAs?si=emi1kDsJ81-2-tc3)

re-agent automates a reverse-engineering workflow by combining a reverser/checker loop with Ghidra decompilation through [ghidra-ai-bridge](https://github.com/dryxio/ghidra-ai-bridge). The current pipeline also retrieves nearby project source context during generation and runs a conservative structural verifier before accepting checker passes.

```
re-agent reverse --class CTrain
    │
    ├── Config (re-agent.yaml + env + CLI)
    │   └── project_profile (stub_markers, hook_patterns, source_layout)
    │
    ├── Orchestrator (single / class runner)
    │   ├── Function Picker (ranks by caller count, filters completed)
    │   ├── Context Gatherer (decompile + xrefs + structs + source retrieval)
    │   │
    │   ├── Agent Loop (reverser → checker → fix, max N rounds)
    │   │   ├── LLM Providers: LiteLLM vendors (Anthropic/OpenAI/Gemini/…) | claude-code | codex | antigravity | agentify
    │   │   ├── Separate reverser (`llm`) and checker (`checker_llm`) models; ordered fallback chains
    │   │   └── Prompt Templates (customizable .md files)
    │   │
    │   ├── Objective Verifier (call-count + control-flow sanity checks)
    │   │
    │   ├── Parity Engine (GREEN/YELLOW/RED verification gate)
    │   │   ├── Source Indexer (C++ body parser)
    │   │   ├── 11 Heuristic Signals (all configurable/toggleable)
    │   │   └── Semantic Rules + Manual Approvals
    │   │
    │   └── Session State (JSON progress file)
    │
    └── RE Backend: ghidra-ai-bridge
        └── Capability flags → graceful degradation
```

## Requirements

- Python 3.10+
- [ghidra-ai-bridge](https://github.com/Dryxio/ghidra-ai-bridge) — re-agent uses this as its backend to decompile functions, fetch xrefs, read structs/enums, and query Ghidra. Install it and point it at your Ghidra project before running `re-agent reverse`.
- One supported LLM setup (any of):
  - An API key for a LiteLLM vendor: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, … (set `base_url` for OpenAI-compatible endpoints)
  - A logged-in CLI: `claude-code` (`~/.claude/`), `codex` (ChatGPT login), or `antigravity` (`agy`, Google sign-in) — no API key
  - `agentify` — drives a logged-in browser AI session via the Agentify Desktop MCP server (needs Node 20+ and the `agentify` extra)

## Installation

```bash
pip install re-agent
# optional extras:
#   pip install "re-agent[agentify]"   # Agentify (MCP) browser-session provider
#   pip install "re-agent[ghidra-bridge]"
```

## Quick Start

```bash
# 1. Initialize project config
re-agent init

# 2. Edit re-agent.yaml with your project settings

# 3. Reverse a single function
re-agent reverse --address 0x6F86A0

# 4. Reverse all functions in a class
re-agent reverse --class CTrain --max-functions 10

# 5. Run parity checks
re-agent parity --address 0x6F86A0

# 6. Check progress
re-agent status
```

## Configuration

re-agent uses a layered configuration system (highest priority first): CLI flags > environment variables (`RE_AGENT_*`) > `re-agent.yaml` > defaults.

```yaml
llm:                         # the reverser (generation) model
  provider: anthropic        # any LiteLLM vendor (anthropic | openai | gemini | ollama | mistral | openrouter | ...)
                             # or a CLI provider: claude-code | antigravity | codex | agentify
                             # or 'litellm' to let `model` carry the full route
  model: claude-opus-4-8     # null = let the provider pick its own default
  # api_key: set via RE_AGENT_LLM_API_KEY env var
  timeout_s: 1800
  # API-tier tuning: reasoning_effort / thinking / extra_params
  # CLI-tier tuning: extra_args / env  (e.g. env: {MAX_THINKING_TOKENS: "10000"})
  # Ordered fallback chain, tried on transient errors (full history replayed on switch):
  fallbacks:
    - { provider: codex, model: gpt-5.5 }
    - { provider: agentify, model: chatgpt }

checker_llm:                 # optional separate checker (review) model; reuses `llm` when omitted
  provider: agentify
  model: chatgpt

backend:
  type: ghidra-bridge
  cli_path: ~/ghidra-tools/ghidra

orchestrator:
  max_review_rounds: 4
  max_functions_per_class: 10
  objective_verifier_enabled: true

project_profile:
  source_root: ./source/game_sa
  hook_patterns:
    - 'RH_ScopedInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)'
  stub_markers: ["NOTSA_UNREACHABLE"]
  stub_call_prefix: "plugin::Call"
```

See [docs/configuration.md](docs/configuration.md) for all options.

## CLI Reference

| Command | Description |
|---------|-------------|
| `re-agent init` | Generate `re-agent.yaml` config file |
| `re-agent agentify-login` | Pre-warm Agentify browser sessions (one-time sign-in) |
| `re-agent reverse --address ADDR` | Reverse a single function |
| `re-agent reverse --class CLASS` | Reverse all functions in a class |
| `re-agent reverse --dry-run` | Show what would be reversed |
| `re-agent parity --address ADDR` | Run parity checks on a function |
| `re-agent parity --filter REGEX` | Run parity checks matching pattern |
| `re-agent status` | Show reversal progress |
| `re-agent status --class CLASS` | Show progress for a specific class |

## LLM Providers

API providers all go through **LiteLLM** — set `provider` to the vendor name and `model` to the bare id:

- **Anthropic / OpenAI / Gemini / Ollama / Mistral / OpenRouter / …** — set the matching env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, …); use `provider: openai` + `base_url` for OpenAI-compatible endpoints, or `provider: litellm` to let `model` carry the full route.
- API-tier reasoning/thinking is set per agent via `reasoning_effort`, `thinking`, and `extra_params`.

CLI providers shell out to a local tool and need **no API key**:

- **claude-code** — `claude --print` (`~/.claude/` auth); thinking via `env: {MAX_THINKING_TOKENS: …}`
- **codex** — `codex exec` (ChatGPT login); reasoning via `extra_args: ["-c", "model_reasoning_effort=high"]`
- **antigravity** — `agy -p` (Google sign-in); model via `--model`
- **agentify** — drives a logged-in ChatGPT/Claude/Gemini/Perplexity/Grok/AI-Studio **browser session** through the Agentify Desktop MCP server (`npx -y @agentify/desktop mcp`). `model` is a vendor hint (`chatgpt`, `claude`, …). Install the `agentify` extra and run `re-agent agentify-login` once to sign in.

**Per-role + resilience:** `llm` is the reverser and `checker_llm` (optional) the checker; either may
declare an ordered `fallbacks:` list that fails over on transient errors (rate-limit / 5xx / timeout),
replaying full conversation history on each switch. See [docs/configuration.md](docs/configuration.md).

## Parity Engine

The parity engine runs 11 configurable heuristic signals to verify reversed code matches the original binary:

| Signal | Level | Description |
|--------|-------|-------------|
| Missing source | RED | No source body found for hooked function |
| Stub markers | RED | Source contains stub markers (e.g., NOTSA_UNREACHABLE) |
| Trivial stub | RED | Plugin-call heavy with tiny body and no control flow |
| Large ASM tiny source | RED | ASM >= 80 instructions but source <= 12 lines |
| Plugin-call heavy | YELLOW | Plugin calls dominate the function body |
| Short body | YELLOW | Body has fewer than 6 lines |
| Low call count | YELLOW | Decompile shows many callees but source has few |
| FP sensitivity | YELLOW | ASM has floating-point ops but source doesn't |
| Call count mismatch | YELLOW | Source call count differs significantly from ASM |
| NaN logic | YELLOW | Decompile has NaN handling but source doesn't |
| Inline wrapper | INFO | Function is a thin inline wrapper |

## Objective Verifier

The reversal loop also runs a conservative structural verifier after the LLM checker passes. It only blocks acceptance on strong mismatches such as:

- call-count gaps between candidate code and decompile/ASM
- control-flow gaps where the candidate is clearly missing branches or loops

This is intentionally narrower than full equivalence checking, but it catches obvious false positives before they are recorded as successful reversals.

This matters in practice because an LLM checker can still false-positive on code that looks plausible while missing real branch or call structure from the binary.

## Safety

- **No auto-commit**: re-agent writes code but never commits or pushes
- **Bounded retries**: Hard cap on fix loop iterations (default: 4)
- **Deterministic logs**: Every LLM call logged with timestamps
- **No destructive ops**: Never deletes files, modifies git, or runs builds
- **Session isolation**: Progress appended, never overwritten

## Development

```bash
git clone https://github.com/dryxio/auto-re-agent.git
cd auto-re-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/
ruff check src/
mypy src/re_agent/
```

## License

MIT
