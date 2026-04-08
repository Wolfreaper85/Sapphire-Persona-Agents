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

## Building Your Own Team

The starter team is just a starting point. You can replace it entirely with your own custom personas — fictional characters, original personalities, themed agents, whatever fits your style.

### Step 1: Create a Persona

In the Sapphire UI, go to **Personas** and create a new one. Give it a name, avatar, voice, and trim color. The key fields for delegation:

- **Name** — the internal ID (lowercase, underscores). Example: `iron_man`, `detective`, `my_researcher`
- **Tagline** — short description shown on the roster card
- **Prompt** — the personality prompt that defines how this persona talks, thinks, and approaches tasks
- **Toolset** — determines what this persona can actually *do* (see below)
- **Voice** — any Kokoro voice (`af_heart`, `am_onyx`, `bf_emma`, etc.)
- **Trim Color** — hex color for their chat bubbles and UI accents

### Step 2: Create or Assign a Toolset

Toolsets control what tools each persona has access to. You can:

- **Use a starter toolset** — assign `pa_researcher`, `pa_engineer`, or `pa_sysadmin` directly
- **Create a custom toolset** — in the Agents view, click any roster card to open the Toolset Editor. Pick the tools you want, save as a new toolset.
- **Edit `user/toolsets/toolsets.json` directly** — add a new entry with a `functions` array:

```json
{
  "my_hacker": {
    "functions": [
      "run_command",
      "web_search",
      "get_website",
      "check_internet",
      "get_external_ip",
      "website_status",
      "notepad_read",
      "notepad_append_lines"
    ]
  }
}
```

The plugin auto-detects capabilities from the function names in your toolset. You don't need to register or configure anything — if your toolset has `run_command`, the roster automatically shows "can: run commands & scripts". Name the toolset whatever you want.

### Step 3: Make One Persona the Coordinator

Your lead persona needs `delegate_task` in their toolset. This is what makes them a coordinator instead of a worker. The `pa_coordinator` starter toolset has this, or add it to any custom toolset:

```json
{
  "my_lead": {
    "functions": [
      "delegate_task",
      "check_delegates",
      "get_delegate_result",
      "save_memory",
      "search_memory",
      "list_goals",
      "get_time"
    ]
  }
}
```

**Important**: Keep task-execution tools (like `web_search`, `run_command`) OUT of your coordinator's toolset. If the coordinator has those tools, they'll do the work themselves instead of delegating. Give them only coordination + utility tools.

### Step 4: Talk to Your Lead

Switch to your coordinator persona and ask something that requires specialist work:

> "Check if my internet connection is healthy and get my external IP, then research the best public DNS servers available right now."

Your coordinator will see the team roster (auto-generated), pick the right specialists, delegate, and summarize the results.

### Tips

- **Use characters you love** — fictional characters, historical figures, or original personalities all work. The persona prompt defines their personality; the toolset defines their abilities.
- **Toolset = role, not personality** — Two personas can share the same toolset with different prompts. A "Sherlock Holmes" and a "Nancy Drew" can both use a `researcher` toolset but investigate in completely different styles.
- **Split, don't stack** — Instead of one persona with all tools, create focused specialists. A coordinator + 3-4 specialists works better than one persona trying to do everything.
- **The Toolset Editor is your friend** — Click any roster card in the Agents view to add/remove tools, switch toolsets, or save new ones. No JSON editing required.

## Detected Capabilities

The plugin reads function names from each toolset and auto-generates capability descriptions for the roster. No configuration needed — just put the right functions in your toolset.

| Function | Detected Capability |
|----------|-------------------|
| `run_command` | run commands & scripts |
| `web_search` | search the web |
| `get_website` | read websites |
| `research_topic` | research topics in depth |
| `get_wikipedia` | read Wikipedia |
| `check_internet` | check network health |
| `get_external_ip` | get external IP |
| `ha_activate` | control smart home |
| `ha_set_light` | control lights |
| `ha_set_thermostat` | adjust thermostat |
| `tandem_browse` | browse with Tandem browser |
| `create_full_persona` | create personas |
| `delegate_task` | delegate to other agents |

Personas whose toolset has none of these functions are labeled "chat only" and won't receive delegations.

## Requirements

- [Sapphire AI](https://github.com/sapphire-ai) — core platform
- Python 3.10+
- At least one configured LLM provider

## License

[MIT](LICENSE)
