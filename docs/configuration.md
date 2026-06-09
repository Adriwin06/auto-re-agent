# Configuration

re-agent is configured via `re-agent.yaml`, environment variables, and CLI flags.

## Priority Order

CLI flags > Environment variables > YAML config > Defaults

## Environment Variables

| Variable | Maps to |
|----------|---------|
| `RE_AGENT_LLM_PROVIDER` | `llm.provider` |
| `RE_AGENT_LLM_API_KEY` | `llm.api_key` |
| `RE_AGENT_LLM_MODEL` | `llm.model` |
| `RE_AGENT_LLM_BASE_URL` | `llm.base_url` |
| `RE_AGENT_BACKEND_CLI_PATH` | `backend.cli_path` |
| `RE_AGENT_BACKEND_TIMEOUT` | `backend.timeout_s` |

## LLM Config

```yaml
llm:
  provider: "anthropic"     # any LiteLLM vendor (anthropic|openai|gemini|ollama|mistral|openrouter|...),
                            # a CLI provider (claude-code|antigravity|codex), or 'litellm' (model carries the route)
  model: "claude-opus-4-8"
  api_key: null
  base_url: null
  max_tokens: 4096
  temperature: 0.0
  timeout_s: 1800
```

Notes:

- API providers go through LiteLLM: set `provider` to the LiteLLM vendor name (`anthropic`, `openai`, `gemini`, `ollama`, `mistral`, `openrouter`, …) and `model` to the bare id. Auth is read from the matching env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, …)
- For an OpenAI-compatible / self-hosted endpoint, use `provider: openai` plus `base_url`
- `provider: litellm` is an escape hatch — `model` then carries the full route itself (e.g. `openrouter/anthropic/claude-opus-4-8`)
- CLI providers do not use LiteLLM and need no API key: `claude-code` (`~/.claude/`), `antigravity` (Google Sign-In, needs `agy`), `codex` (ChatGPT login)

## Project Profile

The `project_profile` section makes re-agent work across different RE projects:

```yaml
project_profile:
  hook_patterns:
    - 'RH_ScopedInstall\s*\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)'
  stub_markers: ["NOTSA_UNREACHABLE"]
  stub_call_prefix: "plugin::Call"
  source_root: "./source/game_sa"
  source_extensions: [".cpp", ".h", ".hpp"]
```

## Parity Config

```yaml
parity:
  enabled: true
  call_count_warn_diff: 3
  inline_wrapper_autoskip: false
```

## Orchestrator Config

```yaml
orchestrator:
  max_review_rounds: 4
  max_functions_per_class: 10
  objective_verifier_enabled: true
  objective_call_count_tolerance: 3
  objective_control_flow_tolerance: 2
```
