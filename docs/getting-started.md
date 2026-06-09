# Getting Started

## Installation

```bash
pip install re-agent
# optional extras: pip install "re-agent[agentify]"  (Agentify browser-session provider)
```

## Quick Start

1. Initialize configuration:
```bash
re-agent init
```

2. Edit `re-agent.yaml` with your LLM provider/key and Ghidra bridge path. The `llm` block is the
   reverser; add an optional `checker_llm` for a separate review model. See
   [configuration.md](configuration.md) for providers, fallbacks, and reasoning/thinking options.

3. (Only if using `provider: agentify`) Sign into the browser session once so later runs don't block:
```bash
re-agent agentify-login
```

4. Reverse a single function:
```bash
re-agent reverse --address 0x6F86A0 --class CTrain
```

5. Reverse a full class:
```bash
re-agent reverse --class CTrain --max-functions 5
```

6. Run parity checks:
```bash
re-agent parity --limit 50
```

7. Check progress:
```bash
re-agent status
```
