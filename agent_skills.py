# Agent Skills — Per-persona skill definitions
#
# Each persona gets a skills.md file that defines:
#   - Their role on the team
#   - When to use each of their tools
#   - What they should NOT do (boundary awareness)
#   - Tips and preferences
#   - (Optional) YAML frontmatter with triggers, capabilities, and role metadata
#
# Loaded into the delegation prompt so the agent knows their job
# before they start working. Editable from the persona card UI.

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent.parent / 'user' / 'personas' / 'skills'

# ── Role → trigger keyword mapping ────────────────────────────────────────────
# Used by generate_skills() to auto-derive triggers from detected role
_ROLE_TRIGGERS = {
    'Smart Home Controller': ['smart home', 'lights', 'thermostat', 'temperature', 'home assistant', 'automation', 'switch'],
    'System Administrator': ['server', 'network', 'diagnose', 'uptime', 'connectivity', 'dns', 'port', 'firewall', 'sysadmin'],
    'System Operator': ['system', 'diagnostics', 'server', 'network', 'troubleshoot', 'status'],
    'Engineer': ['build', 'code', 'script', 'fix', 'debug', 'implement', 'deploy', 'compile', 'install', 'program'],
    'Researcher': ['research', 'investigate', 'find out', 'look up', 'dig into', 'fact check', 'summarize', 'news', 'article'],
    'Browser Operator': ['browse', 'web page', 'click', 'form', 'screenshot', 'navigate'],
    'Persona Architect': ['create persona', 'design persona', 'new character', 'new agent'],
    'Versatile Specialist': ['research', 'build', 'code', 'investigate', 'fix', 'deploy'],
    'Financial Analyst': ['stock', 'dividend', 'portfolio', 'earnings', 'market', 'invest', 'finance', 'yield', 'etf'],
}

# ── Approach patterns by role ─────────────────────────────────────────────────
_ROLE_APPROACHES = {
    'Researcher': [
        ('Research tasks', 'Search broadly first with web_search, then drill into the top 2-3 results with get_website. Cross-reference facts across multiple sources before reporting.'),
        ('Fact-checking', 'Verify claims against at least 2 independent sources. Note any contradictions.'),
        ('News gathering', 'Search for the most recent coverage, check multiple outlets, and distinguish fact from opinion.'),
    ],
    'Engineer': [
        ('Building/coding', 'Understand the requirement first, check existing code if relevant, then implement. Test your work with run_command before reporting success.'),
        ('Debugging', 'Reproduce the issue first, check logs, isolate the cause, then fix. Verify the fix works.'),
        ('Scripts & automation', 'Write clean, commented code. Handle edge cases. Test before reporting done.'),
    ],
    'System Administrator': [
        ('Diagnostics', 'Check connectivity first (ping, DNS), then service status, then logs. Report findings in order.'),
        ('Server work', 'Verify current state before making changes. Always have a rollback plan.'),
        ('Network issues', 'Start with check_internet, then trace the path: DNS → routing → service health.'),
    ],
    'System Operator': [
        ('System checks', 'Start with broad health checks, then narrow down to specific subsystems.'),
        ('Troubleshooting', 'Gather symptoms first, check logs, then apply targeted fixes.'),
    ],
    'Smart Home Controller': [
        ('Device control', 'Confirm the target area/device exists with ha_list_areas or ha_house_status before sending commands.'),
        ('Automations', 'Check current state first, then activate. Verify the change took effect.'),
    ],
    'Browser Operator': [
        ('Web interaction', 'Navigate to the page, read its content, then interact. Verify actions took effect.'),
    ],
    'Financial Analyst': [
        ('Stock research', 'Search for current data first, then deep-dive into fundamentals. Cross-reference multiple financial sources.'),
        ('Portfolio analysis', 'Gather current prices and yields, calculate metrics, present a clear summary with sources.'),
        ('Market news', 'Check multiple financial news sources, distinguish analysis from reporting, note the publication date.'),
    ],
    'Versatile Specialist': [
        ('Mixed tasks', 'Identify which parts need research vs. implementation. Handle research first to inform the build.'),
    ],
}


def _ensure_dir():
    """Create skills directory if it doesn't exist."""
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)


# ── YAML Frontmatter Parser ──────────────────────────────────────────────────
# Lightweight parser — no pyyaml dependency. Handles simple key: value and
# key:\n  - item lists (enough for our frontmatter needs).

def parse_frontmatter(content):
    """Parse optional YAML frontmatter from a skills.md file.

    Returns (metadata_dict, body_text).
    If no frontmatter, returns ({}, original_content).
    """
    if not content or not content.startswith('---'):
        return {}, content

    # Find the closing ---
    end = content.find('\n---', 3)
    if end == -1:
        return {}, content

    yaml_block = content[3:end].strip()
    body = content[end + 4:].strip()  # skip past \n---

    metadata = {}
    current_key = None
    current_list = None

    for line in yaml_block.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # List item under current key
        if stripped.startswith('- ') and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue

        # Key: value pair
        match = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
        if match:
            # Save previous list if we had one
            if current_key and current_list is not None:
                metadata[current_key] = current_list

            key = match.group(1)
            value = match.group(2).strip()

            if value:
                # Inline value
                metadata[key] = value
                current_key = None
                current_list = None
            else:
                # Empty value — expect list items on next lines
                current_key = key
                current_list = []

    # Save final list if we had one
    if current_key and current_list is not None:
        metadata[current_key] = current_list

    return metadata, body


def get_triggers(persona_name):
    """Get trigger keywords from a persona's skills frontmatter. Returns [] if none."""
    content = get_skills(persona_name)
    if not content:
        return []
    meta, _ = parse_frontmatter(content)
    return meta.get('triggers', [])


def get_all_triggers():
    """Get triggers for all personas with skills. Returns {persona_name: [triggers]}."""
    _ensure_dir()
    result = {}
    for p in _SKILLS_DIR.glob("*.md"):
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding='utf-8').strip()
            meta, _ = parse_frontmatter(content)
            triggers = meta.get('triggers', [])
            if triggers:
                result[p.stem] = triggers
        except Exception as e:
            logger.debug(f"[AGENT-SKILLS] Failed to read triggers from {p.stem}: {e}")
    return result


def get_skills(persona_name):
    """Load a persona's skills.md content. Returns empty string if none exists."""
    persona_name = persona_name.lower().strip()
    path = _SKILLS_DIR / f"{persona_name}.md"
    try:
        if path.exists():
            return path.read_text(encoding='utf-8').strip()
    except Exception as e:
        logger.warning(f"[AGENT-SKILLS] Failed to read skills for {persona_name}: {e}")
    return ""


def save_skills(persona_name, content):
    """Save a persona's skills.md content."""
    persona_name = persona_name.lower().strip()
    if not persona_name:
        return False

    _ensure_dir()
    path = _SKILLS_DIR / f"{persona_name}.md"
    try:
        path.write_text(content.strip() + "\n", encoding='utf-8')
        logger.info(f"[AGENT-SKILLS] Saved skills for {persona_name} ({len(content)} chars)")
        return True
    except Exception as e:
        logger.error(f"[AGENT-SKILLS] Failed to save skills for {persona_name}: {e}")
        return False


def delete_skills(persona_name):
    """Delete a persona's skills file."""
    persona_name = persona_name.lower().strip()
    path = _SKILLS_DIR / f"{persona_name}.md"
    try:
        if path.exists():
            path.unlink()
            logger.info(f"[AGENT-SKILLS] Deleted skills for {persona_name}")
            return True
    except Exception as e:
        logger.warning(f"[AGENT-SKILLS] Failed to delete skills for {persona_name}: {e}")
    return False


def list_all_skills():
    """List all personas that have skills files."""
    _ensure_dir()
    return {
        p.stem: p.read_text(encoding='utf-8').strip()
        for p in _SKILLS_DIR.glob("*.md")
        if p.is_file()
    }


def get_skills_for_prompt(persona_name):
    """Get skills formatted for injection into the delegation prompt.

    If no skills.md exists, auto-generates one from the persona's toolset
    on first delegation so every agent starts with role awareness.

    Strips YAML frontmatter before injection — the agent sees only the
    prose body, not the metadata (triggers/capabilities are for the system).

    Returns a formatted block or empty string.
    """
    content = get_skills(persona_name)
    if not content:
        # Auto-generate on first delegation
        content = generate_skills(persona_name)
        if content:
            save_skills(persona_name, content)
            logger.info(f"[AGENT-SKILLS] Auto-generated skills for {persona_name}")

    if not content:
        return ""

    # Strip frontmatter — agent doesn't need the YAML metadata
    _, body = parse_frontmatter(content)
    if not body:
        body = content  # Fallback if parse somehow empties it

    return (
        "[Your Role & Skills — read this carefully]\n"
        f"{body}\n"
        "Follow these guidelines throughout your task."
    )


def generate_skills(persona_name):
    """Auto-generate a skills.md from a persona's toolset.

    Analyzes which tools the persona has and creates a skills definition with:
    - YAML frontmatter (role, triggers, capabilities)
    - Role description
    - Approach patterns (how to tackle common task types)
    - Tool guidelines
    - Boundaries
    """
    persona_name = persona_name.lower().strip()

    # Load persona data
    try:
        from core.personas.persona_manager import persona_manager
        from core.toolsets import toolset_manager

        persona_data = persona_manager.get(persona_name)
        if not persona_data or not isinstance(persona_data, dict):
            return ""

        settings = persona_data.get('settings', {})
        display_name = persona_data.get('name', persona_name)
        toolset = settings.get('toolset', 'conversation')

        if toolset in ('conversation', 'personality'):
            return ""  # Chat-only personas don't need skills

        # Get actual tool names
        tool_names = set()
        if toolset == 'all':
            tool_names = {'run_command', 'web_search', 'get_website'}
        elif toolset_manager.toolset_exists(toolset):
            tool_names = set(toolset_manager.get_toolset_functions(toolset))

        if not tool_names:
            return ""

    except Exception as e:
        logger.warning(f"[AGENT-SKILLS] Failed to load persona data for generation: {e}")
        return ""

    # Detect role from tools
    has_commands = 'run_command' in tool_names or 'execute_code' in tool_names
    has_research = 'web_search' in tool_names or 'research_topic' in tool_names
    has_network = 'check_internet' in tool_names or 'website_status' in tool_names
    has_smarthome = 'ha_activate' in tool_names or 'ha_set_light' in tool_names
    has_browser = 'tandem_browse' in tool_names
    has_persona_create = 'create_full_persona' in tool_names
    has_finance = 'save_knowledge' in tool_names and has_research

    # Determine primary role
    if has_smarthome:
        role = "Smart Home Controller"
        role_desc = "You control smart home devices — lights, thermostat, switches, and automations."
    elif has_finance:
        role = "Financial Analyst"
        role_desc = "You research investments, analyze markets, and track portfolio performance."
    elif has_network and has_commands and not has_research:
        role = "System Administrator"
        role_desc = "You handle infrastructure, network diagnostics, and system operations."
    elif has_network and has_commands:
        role = "System Operator"
        role_desc = "You handle system operations, diagnostics, and technical problem-solving."
    elif has_commands and not has_research:
        role = "Engineer"
        role_desc = "You build, code, script, and handle technical implementation."
    elif has_research and not has_commands:
        role = "Researcher"
        role_desc = "You find, verify, and synthesize information from the web."
    elif has_browser:
        role = "Browser Operator"
        role_desc = "You interact with web pages — browsing, form filling, and data extraction."
    elif has_persona_create:
        role = "Persona Architect"
        role_desc = "You create and design new personas for the team."
    elif has_commands and has_research:
        role = "Versatile Specialist"
        role_desc = "You handle a mix of technical tasks and research."
    else:
        role = "Specialist"
        role_desc = "You handle specific tasks within your toolset."

    # ── Build YAML frontmatter ────────────────────────────────────────────────
    triggers = _ROLE_TRIGGERS.get(role, [])
    capabilities = sorted(tool_names & {
        'run_command', 'execute_code', 'web_search', 'research_topic',
        'get_website', 'check_internet', 'ha_activate', 'ha_set_light',
        'ha_set_thermostat', 'tandem_browse', 'create_full_persona',
        'get_images', 'get_youtube_transcript', 'save_knowledge',
    })

    frontmatter_lines = [
        '---',
        f'role: {role}',
    ]
    if triggers:
        frontmatter_lines.append('triggers:')
        for t in triggers:
            frontmatter_lines.append(f'  - {t}')
    if capabilities:
        frontmatter_lines.append('capabilities:')
        for c in capabilities:
            frontmatter_lines.append(f'  - {c}')
    frontmatter_lines.append('---')
    frontmatter = '\n'.join(frontmatter_lines)

    # ── Build approach patterns ───────────────────────────────────────────────
    approaches = _ROLE_APPROACHES.get(role, [])
    approach_section = ""
    if approaches:
        approach_lines = []
        for label, guidance in approaches:
            approach_lines.append(f"- **{label}**: {guidance}")
        approach_section = "## Approach Patterns\n" + "\n".join(approach_lines) + "\n"

    # ── Build tool guidelines ─────────────────────────────────────────────────
    tool_lines = []
    tool_guidance = {
        'run_command': 'Execute shell commands, scripts, and system operations.',
        'execute_code': 'Run code snippets directly.',
        'ask_claude': 'Get help with complex code, analysis, or brainstorming.',
        'web_search': 'Search the web for information.' + (' ONLY for technical docs and error lookups — not general research.' if has_commands and not has_research else ''),
        'research_topic': 'Deep multi-source research on a topic.',
        'get_website': 'Read full web page content.' + (' ONLY for technical documentation.' if has_commands and not has_research else ''),
        'get_wikipedia': 'Quick factual lookups and background context.',
        'get_site_links': 'Explore a site\'s structure to find specific pages.',
        'get_images': 'Find images when specifically requested.',
        'check_internet': 'Quick connectivity verification.',
        'get_external_ip': 'Check public IP and network identity.',
        'website_status': 'Fast HTTP health check — use before manual curl.',
        'ha_activate': 'Execute Home Assistant automations.',
        'ha_set_light': 'Control lights — on/off, brightness, color.',
        'ha_area_light': 'Control all lights in an area.',
        'ha_set_thermostat': 'Set temperature and HVAC mode.',
        'ha_set_switch': 'Toggle switches and smart plugs.',
        'ha_list_areas': 'List available rooms/areas.',
        'ha_house_status': 'Full home state overview.',
        'tandem_browse': 'Open and interact with web pages.',
        'tandem_read_page': 'Read content from the current page.',
        'create_full_persona': 'Create a complete new persona.',
        'research_character': 'Research a character for persona creation.',
        'save_knowledge': 'Save important findings to long-term memory.',
    }

    for tool_name in sorted(tool_names):
        guidance = tool_guidance.get(tool_name)
        if guidance:
            tool_lines.append(f"- **{tool_name}**: {guidance}")

    # Build boundaries
    boundaries = []
    if not has_research:
        boundaries.append("- Don't do general web research or news gathering — that's a researcher's job.")
    if not has_commands:
        boundaries.append("- Don't run commands or modify systems — that's an engineer/sysadmin's job.")
    boundaries.append("- If a task is outside your specialty, say so and suggest the right type of specialist.")

    tools_section = "\n".join(tool_lines) if tool_lines else "- Use the tools available in your toolset."
    boundaries_section = "\n".join(boundaries) if boundaries else "- Stay within your area of expertise."

    content = f"""{frontmatter}

# {display_name} — {role}

{approach_section}## Your Role
{role_desc}

## When to Use Your Tools
{tools_section}

## What You Should NOT Do
{boundaries_section}

## Tips
- If you find something other agents need to know, use shared_context_write.
- If you learn something useful, use record_lesson to remember it for next time.
"""
    return content.strip()
