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
    'Secretary': ['calendar', 'schedule', 'meeting', 'reminder', 'alarm', 'event', 'appointment', 'deadline', 'plan', 'habit', 'goal', 'note', 'journal', 'organize', 'laundry', 'todo', 'errand'],
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
    'Secretary': [
        ('Calendar events', 'ONE create_event call per request. Set start_time to when the event happens, use reminder_minutes for advance alerts (e.g. reminder_minutes=60 for 1-hour warning). NEVER create separate alarm events — use reminder_minutes instead.'),
        ('Reminders & alarms', 'An "alarm" or "alert" before an event = reminder_minutes on that event, NOT a second event. 1 hour before = reminder_minutes=60. 30 min before = reminder_minutes=30.'),
        ('Goals & habits', 'Use add_user_goal for long-term objectives, create_habit for recurring daily/weekly tasks. Use toggle_habit to mark habits done.'),
        ('Daily planning', 'Use manage_daily_plan to set up today\'s focus goals. Use save_daily_note for journal entries.'),
        ('Notes & bulletins', 'Use take_note for quick reference notes. Use post_bulletin for team-visible announcements or pattern proposals.'),
    ],
    'Versatile Specialist': [
        ('Mixed tasks', 'Identify which parts need research vs. implementation. Handle research first to inform the build.'),
    ],
}

# ── Tandem-aware approach overrides (used when tandem tools detected) ────────
_ROLE_APPROACHES_TANDEM = {
    'Researcher': [
        ('Research tasks', 'Search broadly with tandem_search or web_search, then drill into results with tandem_browse. Read content with tandem_read_page. Cross-reference across multiple sources before reporting.'),
        ('Fact-checking', 'Verify claims against at least 2 independent sources. Note any contradictions.'),
        ('News gathering', 'Search for recent coverage, check multiple outlets, distinguish fact from opinion.'),
        ('Deep browsing', 'For multi-step research, use tandem_browse → tandem_wait → tandem_read_page → tandem_click_link → tandem_read_page to follow trails.'),
    ],
    'Financial Analyst': [
        ('Stock research', 'Search for current data with tandem_search, then deep-dive via tandem_browse. Financial sites need JavaScript rendering — always use Tandem. Use tandem_extract for tables and numerical data.'),
        ('Portfolio analysis', 'Gather current prices and yields via tandem_browse, use tandem_extract for data tables, present a clear summary with sources.'),
        ('Market news', 'Check multiple financial news sources via tandem_browse, distinguish analysis from reporting, note the publication date.'),
    ],
    'Engineer': [
        ('Building/coding', 'Understand the requirement first, check existing code if relevant, then implement. Test your work with run_command before reporting success.'),
        ('Debugging', 'Reproduce the issue first, check logs, isolate the cause, then fix. Verify the fix works.'),
        ('Documentation lookup', 'Use tandem_browse for documentation sites that need JavaScript. Use tandem_read_page to extract relevant sections.'),
    ],
    'System Administrator': [
        ('Diagnostics', 'Check connectivity first (ping, DNS), then service status, then logs. Report findings in order.'),
        ('Server work', 'Verify current state before making changes. Always have a rollback plan.'),
        ('Documentation', 'Use tandem_browse for web-based documentation and dashboards.'),
    ],
}

# ── Tandem Browser workflow (auto-injected when tandem tools in toolset) ─────
_TANDEM_WORKFLOW = """## Tandem Browser Workflow
Use Tandem Browser as your PRIMARY browsing tool. It renders full pages with JavaScript, handles sessions, and lets you interact with sites.

1. **Navigate**: `tandem_browse` to open a URL
2. **Wait**: `tandem_wait` after browsing to let the page load
3. **Read**: `tandem_read_page` to get page text content
4. **Extract**: `tandem_extract` for structured data (tables, lists)
5. **Follow links**: `tandem_click_link` to follow interesting links
6. **Search on-page**: `tandem_forms` to find search boxes, `tandem_type` to fill them, `tandem_press_key` to submit
7. **Multi-tab**: `tandem_tabs` to list open tabs, `tandem_focus_tab` to switch between them, `tandem_close_tab` when done
8. **Overview**: `tandem_awareness` for a quick page digest, `tandem_context` for active tab summary
9. **Visual**: `tandem_screenshot` when you need to capture what you see
10. **Fallback**: Use `get_website` ONLY when Tandem is unavailable or for simple static pages
"""

# ── MemPalace guidance (auto-injected when mempalace tools in toolset) ───────
_MEMPALACE_GUIDANCE = """## Memory System (MemPalace)
You use MemPalace for persistent memory — semantic retrieval with automatic cross-persona knowledge sharing.

- **memory_remember**: Store important findings, insights, or facts. They persist across sessions.
- **memory_recall**: Retrieve YOUR past memories on a topic (searches your personal wing).
- **memory_search**: Search across ALL personas' memories — use when you need cross-team context.
- **memory_diary**: Write a personal diary entry — reflections, session notes, things you noticed. One per day.
- **vault_write**: Write a note directly to the Obsidian vault — use for structured documentation, guides, or reference material.
- **vault_read**: Read a note from the Obsidian vault — check existing docs before writing new ones.
- Use `memory_remember` for anything worth keeping. If you discovered it and it matters, store it.
- Use `memory_recall` before starting research to check if you already know something.
- Use `memory_diary` for end-of-task reflections or observations that don't fit as factual memories.
- **TTL (rolling memory)**: For time-sensitive data (stock prices, weather, news headlines), use `ttl='7d'` so old data auto-prunes. For permanent facts (preferences, portfolio tickers), omit ttl.
"""


_SECRETARY_GUIDANCE = """## Calendar Rules (CRITICAL)
- **ONE event per request.** Never create multiple events for the same thing.
- **Alarms/reminders = reminder_minutes**, NOT separate events. "Alarm 1 hour before" = `reminder_minutes=60` on the MAIN event, not a second event at an earlier time.
- **start_time = when the thing happens.** If user says "laundry at 10am with alarm 1 hour before", the event is at 10:00 with `reminder_minutes=60`. NOT an event at 9:00.
- Use `category="reminder"` for reminder-type events, `category="event"` for scheduled activities.
- Always use `get_time` first to confirm today's date before creating events.
"""


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
    has_mempalace = 'memory_remember' in tool_names
    has_persona_create = 'create_full_persona' in tool_names
    has_calendar = 'create_event' in tool_names
    has_goals = 'add_user_goal' in tool_names or 'create_goal' in tool_names
    has_habits = 'create_habit' in tool_names
    # Detect financial toolset by checking the toolset name directly
    # (save_knowledge is in most toolsets, so tool-presence alone is unreliable)
    is_finance_toolset = toolset in ('pa_financial', 'financial', 'finance')
    is_secretary_toolset = toolset in ('pa_secretary', 'secretary') or (has_calendar and has_habits)

    # Determine primary role
    if is_secretary_toolset:
        role = "Secretary"
        role_desc = "You manage the user's calendar, goals, habits, daily plans, and notes. You are organized, efficient, and precise."
    elif has_smarthome:
        role = "Smart Home Controller"
        role_desc = "You control smart home devices — lights, thermostat, switches, and automations."
    elif is_finance_toolset:
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
    triggers = list(_ROLE_TRIGGERS.get(role, []))
    if has_browser and 'browse' not in triggers:
        triggers.extend(['browse', 'search for', 'open page', 'navigate to'])
    capabilities = sorted(tool_names & {
        'run_command', 'execute_code', 'web_search', 'research_topic',
        'get_website', 'check_internet', 'ha_activate', 'ha_set_light',
        'ha_set_thermostat', 'tandem_browse', 'tandem_search', 'tandem_read_page',
        'tandem_extract', 'create_full_persona', 'get_images',
        'get_youtube_transcript', 'save_knowledge', 'memory_remember',
        'memory_recall', 'memory_search',
        'create_event', 'update_event', 'delete_event', 'manage_daily_plan',
        'add_user_goal', 'create_habit', 'take_note', 'post_bulletin',
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
    # Use tandem-aware approaches when tandem tools are in the toolset
    if has_browser and role in _ROLE_APPROACHES_TANDEM:
        approaches = _ROLE_APPROACHES_TANDEM[role]
    else:
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
        'tandem_browse': 'Navigate to a URL in Tandem Browser (full JavaScript rendering).',
        'tandem_search': 'Search the web via Tandem Browser.',
        'tandem_read_page': 'Read text content from the current page.',
        'tandem_snapshot': 'Get a DOM snapshot of the page.',
        'tandem_screenshot': 'Capture a visual screenshot of the page.',
        'tandem_click_link': 'Click a link on the page to follow it.',
        'tandem_type': 'Type text into a form field or input.',
        'tandem_scroll': 'Scroll the page up or down.',
        'tandem_wait': 'Wait for the page to finish loading.',
        'tandem_extract': 'Extract structured data (tables, lists) from the page.',
        'tandem_tabs': 'List all open browser tabs.',
        'tandem_links': 'Get all links on the current page.',
        'tandem_forms': 'List all forms on the page (search boxes, login forms, etc.).',
        'tandem_close_tab': 'Close a browser tab.',
        'tandem_status': 'Check if Tandem Browser is running and ready.',
        'tandem_press_key': 'Press a key (Enter, Escape, Tab, arrows).',
        'tandem_awareness': 'Get a quick digest/overview of the current page.',
        'tandem_focus_tab': 'Switch focus to a specific tab.',
        'tandem_context': 'Get a summary of the active tab (title, URL, state).',
        'memory_remember': 'Store findings to MemPalace — persists across sessions. Use ttl="7d" for time-sensitive data (prices, weather, news) so it auto-expires.',
        'memory_recall': 'Retrieve your past memories on a topic.',
        'memory_search': 'Search across all personas\' memories for related knowledge.',
        'memory_diary': 'Write a diary entry (personal reflections, session notes).',
        'vault_write': 'Write to the Obsidian vault.',
        'vault_read': 'Read from the Obsidian vault.',
        'create_full_persona': 'Create a complete new persona.',
        'research_character': 'Research a character for persona creation.',
        'save_knowledge': 'Save important findings to long-term memory.',
        # ── Mission Control tools ──
        'create_event': 'Create a calendar event. Set start_time to the EVENT time, use reminder_minutes for advance alerts. NEVER make a separate event for an alarm — use reminder_minutes instead.',
        'update_event': 'Modify an existing calendar event by ID.',
        'delete_event': 'Remove a calendar event by ID.',
        'manage_daily_plan': 'Create or complete today\'s daily plan with selected goals.',
        'save_daily_note': 'Save a journal entry for today (one per day).',
        'add_user_goal': 'Create a long-term user goal.',
        'complete_goal': 'Mark a goal as done.',
        'create_habit': 'Create a recurring habit to track (daily/weekly).',
        'toggle_habit': 'Mark a habit as done/undone for today.',
        'focus_session': 'Start a timed focus session.',
        'take_note': 'Save a quick reference note to the MC notes board.',
        'search_notes': 'Search through saved notes.',
        'list_notes': 'List all notes on the board.',
        'post_bulletin': 'Post to the team bulletin board.',
        'get_bulletins': 'Read current bulletins.',
        'edit_bulletin': 'Update or resolve a bulletin.',
        'mission_status': 'Get full MC dashboard — goals, habits, events, daily plan overview.',
        'get_learned_rules': 'Read active learned behavior rules.',
        'edit_memory': 'Edit a specific memory entry.',
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

    # ── Build optional capability sections ────────────────────────────────────
    tandem_section = _TANDEM_WORKFLOW if has_browser else ""
    mempalace_section = _MEMPALACE_GUIDANCE if has_mempalace else ""
    secretary_section = _SECRETARY_GUIDANCE if is_secretary_toolset else ""

    # ── Build tips ────────────────────────────────────────────────────────────
    tips = []
    if 'shared_context_write' in tool_names:
        tips.append("- If you find something other agents need to know, use shared_context_write.")
    if has_mempalace:
        tips.append("- Store key findings with memory_remember so they persist across sessions.")
        tips.append("- Use memory_recall before starting work to check if you already know something.")
    elif 'record_lesson' in tool_names:
        tips.append("- If you learn something useful, use record_lesson to remember it for next time.")
    if has_browser:
        tips.append("- Use tandem_screenshot to capture visual evidence of your findings.")
        tips.append("- Use get_website ONLY as a fallback when Tandem is unavailable.")
    tips_section = "\n".join(tips) if tips else "- Stay focused on your assigned task."

    content = f"""{frontmatter}

# {display_name} — {role}

{approach_section}## Your Role
{role_desc}

{tandem_section}{mempalace_section}{secretary_section}## When to Use Your Tools
{tools_section}

## What You Should NOT Do
{boundaries_section}

## Tips
{tips_section}
"""
    return content.strip()
