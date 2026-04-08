# Persona Agents

A delegation plugin for [Sapphire AI](https://github.com/sapphire-ai). Your lead persona coordinates a team of specialist AI agents, each with unique personality, voice, and focused toolsets.

## Features

- **Synchronous Delegation** — Lead persona delegates tasks and blocks until the agent returns results. No manual nudging.
- **Persona-Powered Agents** — Each agent runs with their own personality prompt, voice, and trim color.
- **Focused Toolsets** — Agents get only the tools they need (engineering, web research, system diagnostics, etc.).
- **Real-Time SSE Streaming** — Watch your lead persona think, delegate, and summarize in real-time.
- **Round Table Transcript** — Visual delegation chain with avatars, color-coded bubbles, and timestamps.
- **Thinking Blocks** — See your lead persona's reasoning as they decide who to delegate to.
- **Toolset Editor** — Click any agent card to add/remove tools, switch toolsets, or create new ones.
- **Prompt Injection Roster** — Lead persona automatically knows the team's capabilities via injected context.

## How It Works

1. You talk to your lead persona (e.g. Lexi)
2. She sees the team roster injected into her system prompt
3. She calls `delegate_task(persona, task)` to send work to a specialist
4. The specialist runs with their own prompt + toolset, does the work, reports back
5. Lexi receives the full result inline and summarizes for you

## Plugin Structure

```
persona-agents/
  plugin.json              — Routes and tool registration
  delegation_log.py        — Delegation event logging
  hooks/
    prompt_inject.py       — Injects team roster + notifications into system prompt
  tools/
    persona_agents.py      — Core tools: delegate_task, check_delegates, get_delegate_result
  routes/
    delegation.py          — API endpoints: personas, sessions, log
  web/
    main.js                — Standalone Agents view (transcript, roster, toolset editor)
```

## Toolsets

| Toolset | Tools | Use Case |
|---------|-------|----------|
| `engineering` | 24 | Code, commands, tandem browser, Claude |
| `system` | 16 | Diagnostics, network checks, commands |
| `web_research` | 16 | Web search, scraping, Wikipedia, images |
| `personality` | 23 | Prompts, memory, knowledge, goals |
| `smarthome` | 14 | Home Assistant control |
| `work` | 16 | Research, goals, notepad |

## Requirements

- [Sapphire AI](https://github.com/sapphire-ai) — core platform
- Python 3.10+
- At least one configured LLM provider

## License

[MIT](LICENSE)
