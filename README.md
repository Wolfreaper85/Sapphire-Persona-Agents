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

## Quick Start

1. **Install**: Copy the `persona-agents/` folder into your Sapphire `plugins/` directory
2. **Create a coordinator persona**: You need one persona with `delegate_task` in its toolset — this is your "lead" who assigns work to others
3. **Assign toolsets to your personas**: Give each persona a toolset that matches their role. Personas with task-capable tools (web_search, run_command, etc.) become specialists. Personas with only chat/personality tools are ignored for delegation.
4. **Talk to your coordinator**: Ask them to do something complex — they'll automatically delegate to the right specialist based on detected capabilities

### Starter Team (Auto-Installed)

On first load, the plugin seeds a ready-to-go team of 4 personas, their toolsets, and personality prompts:

| Persona | Role | Toolset | Voice | Color |
|---------|------|---------|-------|-------|
| **Atlas** | Lead coordinator | `pa_coordinator` (22 tools) | Onyx | Gold |
| **Scout** | Web researcher | `pa_researcher` (16 tools) | Nova | Blue |
| **Forge** | Engineer / coder | `pa_engineer` (16 tools) | Eric | Orange |
| **Patch** | System admin | `pa_sysadmin` (16 tools) | Emma | Green |

**Atlas** is the lead — talk to him and he'll delegate to Scout, Forge, and Patch based on what the task needs. All seeding is non-destructive: nothing gets overwritten if you already have personas/toolsets with the same names. After install, edit or delete anything freely.

You can also create your own personas and toolsets. The plugin auto-detects capabilities from function names — if a toolset has `run_command`, the roster shows "can: run commands & scripts". If it has `web_search`, it shows "can: search the web". Custom toolset names work fine.

## How It Works

1. You talk to your lead persona (the one with `delegate_task`)
2. They see the team roster injected into their system prompt — auto-generated from your personas and their toolsets
3. They call `delegate_task(persona, task)` to send work to a specialist
4. The specialist runs with their own prompt + toolset, does the work, reports back
5. The lead receives the full result inline and summarizes for you

## Plugin Structure

```
persona-agents/
  plugin.json              — Routes, tool registration, daemon entry
  daemon.py                — Lifecycle: seeds default toolsets on first install
  delegation_log.py        — Delegation event logging
  defaults/
    toolsets.json           — Starter toolsets (seeded to user config on first load)
    personas.json           — Starter personas (Atlas, Scout, Forge, Patch)
    prompts.json            — Personality prompts for starter personas
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

Any Sapphire toolset works. The plugin detects capabilities automatically from function names. Example built-in toolsets:

| Toolset | Tools | Detected Capabilities |
|---------|-------|-----------------------|
| `engineering` | 24 | run commands & scripts, search the web, browse with Tandem |
| `system` | 16 | run commands & scripts, check network health, get external IP |
| `web_research` | 16 | search the web, read websites, research topics in depth |
| `smarthome` | 14 | control smart home, adjust thermostat, control lights |
| `work` | 16 | search the web, read websites, research topics in depth |
| `personality` | 23 | *chat only — not used for delegation* |

## Requirements

- [Sapphire AI](https://github.com/sapphire-ai) — core platform
- Python 3.10+
- At least one configured LLM provider

## License

[MIT](LICENSE)
