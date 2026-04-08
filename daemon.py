"""
Persona Agents daemon — handles plugin lifecycle.

On first load, seeds a starter team into user config:
  - Toolsets (pa_coordinator, pa_researcher, pa_engineer, pa_sysadmin)
  - Personas (Atlas, Scout, Forge, Patch)
  - Prompts (personality prompt for each persona)

Non-destructive — never overwrites existing items. Uses PluginState to
track seeding so it only runs once per install.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_plugin_loader = None
PLUGIN_DIR = Path(__file__).parent
DEFAULTS_DIR = PLUGIN_DIR / "defaults"


def start(plugin_loader, settings):
    """Called when plugin is loaded/enabled."""
    global _plugin_loader
    _plugin_loader = plugin_loader
    logger.info("[PERSONA-AGENTS] Daemon starting")

    _seed_defaults()

    logger.info("[PERSONA-AGENTS] Daemon started")


def stop():
    """Called when plugin is disabled/unloaded."""
    logger.info("[PERSONA-AGENTS] Daemon stopped")


def _seed_defaults():
    """Seed starter team on first install. Non-destructive — skips existing."""
    try:
        from core.plugin_loader import PluginState
        state = PluginState("persona-agents")

        # Only seed once
        if state.get("defaults_seeded"):
            return

        toolsets_added = _seed_toolsets()
        prompts_added = _seed_prompts()
        personas_added = _seed_personas()

        # Mark as done
        state.save("defaults_seeded", True)

        total = toolsets_added + prompts_added + personas_added
        if total > 0:
            logger.info(
                f"[PERSONA-AGENTS] First-run setup complete: "
                f"{toolsets_added} toolsets, {prompts_added} prompts, "
                f"{personas_added} personas seeded"
            )
        else:
            logger.info("[PERSONA-AGENTS] All defaults already exist, nothing to seed")

    except Exception as e:
        logger.error(f"[PERSONA-AGENTS] Default seeding failed: {e}")


def _seed_toolsets():
    """Seed recommended toolsets. Returns count of items added."""
    from core.toolsets import toolset_manager

    defaults_file = DEFAULTS_DIR / "toolsets.json"
    if not defaults_file.exists():
        return 0

    with open(defaults_file, 'r', encoding='utf-8') as f:
        defaults = json.load(f)

    added = 0
    for name, config in defaults.items():
        if name.startswith('_'):
            continue
        if toolset_manager.toolset_exists(name):
            continue

        functions = config.get('functions', [])
        if not functions:
            continue

        toolset_manager.save_toolset(name, functions)
        added += 1
        logger.info(f"[PERSONA-AGENTS] Seeded toolset: {name} ({len(functions)} tools)")

    return added


def _seed_prompts():
    """Seed personality prompts for starter personas. Returns count added."""
    from core.prompt_crud import save_prompt, get_prompt

    defaults_file = DEFAULTS_DIR / "prompts.json"
    if not defaults_file.exists():
        return 0

    with open(defaults_file, 'r', encoding='utf-8') as f:
        defaults = json.load(f)

    added = 0
    for name, prompt_data in defaults.items():
        if name.startswith('_'):
            continue

        # Don't overwrite existing prompts
        existing = get_prompt(name)
        if existing is not None:
            continue

        success, msg = save_prompt(name, prompt_data, allow_overwrite=False)
        if success:
            added += 1
            logger.info(f"[PERSONA-AGENTS] Seeded prompt: {name}")
        else:
            logger.warning(f"[PERSONA-AGENTS] Failed to seed prompt {name}: {msg}")

    return added


def _seed_personas():
    """Seed starter personas. Returns count added."""
    from core.personas.persona_manager import persona_manager

    defaults_file = DEFAULTS_DIR / "personas.json"
    if not defaults_file.exists():
        return 0

    with open(defaults_file, 'r', encoding='utf-8') as f:
        defaults = json.load(f)

    added = 0
    for name, persona_data in defaults.items():
        if name.startswith('_'):
            continue

        # Don't overwrite existing personas
        if persona_manager.get(name) is not None:
            continue

        if persona_manager.create(name, persona_data):
            added += 1
            logger.info(f"[PERSONA-AGENTS] Seeded persona: {name}")
        else:
            logger.warning(f"[PERSONA-AGENTS] Failed to seed persona: {name}")

    return added
