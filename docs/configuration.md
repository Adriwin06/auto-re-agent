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
| `RE_AGENT_CHECKER_LLM_PROVIDER` | `checker_llm.provider` |
| `RE_AGENT_CHECKER_LLM_API_KEY` | `checker_llm.api_key` |
| `RE_AGENT_CHECKER_LLM_MODEL` | `checker_llm.model` |
| `RE_AGENT_CHECKER_LLM_BASE_URL` | `checker_llm.base_url` |
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

## Separate Checker LLM

The reverser (generation) and checker (review) agents can use different models — e.g. a
strong model to write code and a cheaper/faster one to review it. Set `checker_llm` to any
full LLM config; when omitted, the checker reuses `llm`.

```yaml
llm:
  provider: anthropic
  model: claude-opus-4-8        # strong model writes the C++
checker_llm:
  provider: gemini
  model: gemini-3.1-flash       # cheap model reviews each round
```

## Fallback Chains

Any LLM config (`llm` **or** `checker_llm`) may declare an ordered `fallbacks` list. When the
active provider raises a **transient** error (rate-limit, 5xx, timeout, dropped connection) the
loop fails over to the next provider and retries with the **full conversation history**, so no
context is lost on a vendor switch. Auth errors and other non-transient failures fail fast
(no failover). The chain resets to the primary for each new function target.

```yaml
llm:
  provider: anthropic
  model: claude-opus-4-8
  fallbacks:
    - provider: gemini
      model: gemini-3.1-pro
    - provider: openai
      model: gpt-5.5
```

Fallbacks may mix API (LiteLLM) and CLI providers freely. Nesting is allowed up to 5 levels deep.

## Agentify Desktop (browser-session provider)

`provider: agentify` drives a browser AI session (ChatGPT / Claude / Gemini / Perplexity / Grok
/ AI Studio) through the local [Agentify Desktop](https://www.npmjs.com/package/@agentify/desktop)
MCP server — using a subscription you are already signed into instead of metered API tokens.

```yaml
llm:
  provider: agentify
  model: chatgpt                 # vendor hint: chatgpt | claude | gemini | perplexity | grok | aistudio
  # command: "npx -y @agentify/desktop mcp"   # optional launch-command override
  timeout_s: 1800
```

**Requirements (works out of the box):**

- **Node.js 20+** on PATH — the server is launched on demand via `npx -y @agentify/desktop mcp`
  (no global install needed; override with the `command` key if you prefer a pinned/global install).
- The optional Python extra: `pip install -e ".[agentify]"` (pulls in `mcp`).

**First run:** the first query against a vendor opens a browser window for sign-in / CAPTCHA;
Agentify pauses and resumes automatically once you log in. The stable tab is created
automatically — no manual GUI setup required.

**How it maps:** each re-agent conversation becomes one stable Agentify tab (`key`), so multi-round
fixes continue the same chat; the system prompt is sent once as `promptPrefix`. Agentify is a good
fit as a reverser, a separate `checker_llm`, or a `fallbacks` entry. See
[Agentify Desktop docs](https://www.npmjs.com/package/@agentify/desktop) for browser-backend,
SSO, and privacy details.

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
