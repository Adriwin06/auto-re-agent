# Architecture

re-agent is structured as a layered pipeline:

```
CLI -> Config -> Orchestrator -> Agent Loop -> LLM Providers
                      |              |
                      v              v
              Function Picker    RE Backend (Ghidra)
                      |
                      v
               Parity Engine
```

## Layers

- **CLI**: argparse entry points (init, reverse, parity, status, agentify-login)
- **Config**: YAML + env + CLI overlay, project profiles
- **Orchestrator**: Single function or class-level auto-advance (no LLM of its own — it drives the agents)
- **Agents**: Reverser (`llm`) + Checker (`checker_llm`, optional separate model) with fix loop
- **LLM**: Protocol-based providers — LiteLLM vendors (Anthropic/OpenAI/Gemini/…), CLI providers
  (claude-code / codex / antigravity), and Agentify (browser sessions via MCP). Any provider may be
  wrapped in a `FallbackProvider` for ordered failover on transient errors.
- **Backend**: RE tool abstraction with capability flags
- **Verification**: deterministic Objective Verifier (call-count + control-flow), non-LLM
- **Parity**: 11-signal verification engine with scoring (non-LLM)
- **Reports**: JSON/markdown output, session tracking

> Only two roles call an LLM: the Reverser (`llm`) and the Checker (`checker_llm`). The orchestrator,
> objective verifier, and parity engine are LLM-free.
