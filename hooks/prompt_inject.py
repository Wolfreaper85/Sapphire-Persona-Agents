"""
prompt_inject hook — Injects two things into the system prompt:
1. Available persona-agent roster (who can be delegated to)
2. Completed delegate notifications (so lead persona knows to retrieve results)

Designed to work for ANY user's persona/toolset setup — no hardcoded names.
"""

import sys as _sys
import logging

logger = logging.getLogger(__name__)

MAX_ROSTER_ENTRIES = 15

# ── Capability Detection ────────────────────────────────────────────────────
# Instead of hardcoding toolset names, we detect what a toolset can DO
# by checking which functions it contains. This works for any custom toolset.

# Functions that indicate real task-execution capability
_TASK_FUNCTIONS = {
    # Commands / scripting
    'run_command', 'run_powershell', 'execute_code',
    # Web research
    'web_search', 'get_website', 'get_wikipedia', 'research_topic',
    'get_site_links', 'get_images',
    # Smart home
    'ha_activate', 'ha_list_areas', 'ha_area_light', 'ha_set_thermostat',
    'ha_set_light', 'ha_set_switch', 'ha_house_status',
    # Network / system
    'check_internet', 'get_external_ip', 'website_status',
    # Delegation (coordinator)
    'delegate_task', 'check_delegates', 'get_delegate_result',
    'cancel_delegate', 'send_message',
    # Tandem browser
    'tandem_browse', 'tandem_read_page', 'tandem_search', 'tandem_click_link',
    # Persona creation
    'create_full_persona', 'research_character',
}

# Capability categories — detected from which functions a toolset has
_CAPABILITY_MAP = {
    'run_command':       'run commands & scripts',
    'execute_code':      'execute code',
    'web_search':        'search the web',
    'research_topic':    'research topics in depth',
    'get_website':       'read websites',
    'get_wikipedia':     'read Wikipedia',
    'check_internet':    'check network health',
    'get_external_ip':   'get external IP',
    'ha_activate':       'control smart home',
    'ha_set_thermostat': 'adjust thermostat',
    'ha_set_light':      'control lights',
    'delegate_task':     'delegate to other agents',
    'tandem_browse':     'browse with Tandem browser',
    'create_full_persona': 'create personas',
    'get_images':        'find images',
}


def _get_dlog_func(name):
    """Get a function from the delegation log module (registered by tools on load)."""
    mod = _sys.modules.get("persona_agents_delegation_log")
    if mod:
        return getattr(mod, name, None)
    return None


def _get_shared():
    """Get shared state from the tools module."""
    return _sys.modules.get("persona_agents_shared")


def log_prompt_inject(*a, **kw):
    fn = _get_dlog_func("log_prompt_inject")
    if fn: fn(*a, **kw)


def log_event(*a, **kw):
    fn = _get_dlog_func("log_event")
    if fn: fn(*a, **kw)


def _detect_capabilities(tool_names):
    """Detect what a toolset can do based on its functions. Returns a short description."""
    caps = []
    seen = set()
    for fn_name in tool_names:
        cap = _CAPABILITY_MAP.get(fn_name)
        if cap and cap not in seen:
            caps.append(cap)
            seen.add(cap)
    return caps


def _is_task_capable(tool_names):
    """Check if a toolset has any real task-execution functions (not just chat/memory)."""
    return bool(set(tool_names) & _TASK_FUNCTIONS)


def _is_coordinator(tool_names):
    """Check if this is a coordinator toolset (has delegate_task)."""
    return 'delegate_task' in set(tool_names)


def prompt_inject(event):
    """Inject persona agent roster + completed delegate notifications."""
    try:
        _inject_roster(event)
        _inject_delegate_notifications(event)
    except Exception as e:
        logger.error(f"Persona Agents prompt_inject error: {e}")
        log_event("ERROR", f"prompt_inject failed: {e}")


def _inject_roster(event):
    """Inject the available persona-agent roster."""
    from core.personas.persona_manager import persona_manager
    from core.toolsets import toolset_manager

    # Get current active persona so we don't list ourselves
    # prompt_inject events don't get 'system' in metadata, so use get_system()
    active_persona = None
    active_tools = []
    try:
        from core.api_fastapi import get_system
        sys_obj = get_system()
        if sys_obj and hasattr(sys_obj, 'llm_chat') and sys_obj.llm_chat:
            settings = sys_obj.llm_chat.session_manager.current_settings
            active_persona = settings.get("persona", "")
            active_toolset = settings.get("toolset", "")
            if active_toolset and toolset_manager.toolset_exists(active_toolset):
                active_tools = toolset_manager.get_toolset_functions(active_toolset)
            logger.debug(f"Active persona={active_persona!r}, toolset={active_toolset!r}, tools={len(active_tools)}")
        else:
            logger.debug("No system/llm_chat available")
    except Exception as e:
        logger.debug(f"Active persona detection failed: {e}")

    personas = persona_manager.get_all()
    if not personas:
        logger.debug("No personas found, skipping roster")
        return

    # Only inject if the active persona is a coordinator (has delegate_task)
    # Otherwise this persona can't delegate — no point showing a roster
    if active_tools and not _is_coordinator(active_tools):
        logger.debug(f"Skipping roster — {active_persona!r} is not a coordinator")
        return

    # Build candidate list, separating specialists from chat-only personas
    specialists = []
    chat_only = []

    for name, p in personas.items():
        if not isinstance(p, dict):
            continue
        # Skip the active persona (you don't delegate to yourself)
        if name == active_persona:
            continue

        settings = p.get("settings", {})
        tagline = p.get("tagline", "")
        toolset = settings.get("toolset", "conversation")

        # Get the actual tool function names
        tool_names = []
        if toolset == "all":
            tool_names = ['run_command', 'web_search', 'get_website']  # representative
        elif toolset_manager.toolset_exists(toolset):
            tool_names = toolset_manager.get_toolset_functions(toolset)

        tool_count = len(tool_names) if toolset != "all" else 106

        # Detect capabilities from actual functions
        if _is_task_capable(tool_names):
            caps = _detect_capabilities(tool_names)
            cap_str = f" — can: {', '.join(caps[:4])}" if caps else ""
            line = f"  - {name} [{toolset}, {tool_count} tools]{cap_str}"
            specialists.append(line)
        else:
            desc = f' — "{tagline}"' if tagline else ""
            line = f"  - {name}{desc} [chat only, no task tools]"
            chat_only.append(line)

    # Specialists always shown; only add chat-only if we have room
    roster_lines = specialists[:MAX_ROSTER_ENTRIES]
    remaining_slots = MAX_ROSTER_ENTRIES - len(roster_lines)
    if remaining_slots > 0 and chat_only:
        roster_lines.extend(chat_only[:remaining_slots])

    skipped = len(specialists) + len(chat_only) - len(roster_lines)
    if skipped > 0:
        roster_lines.append(f"  ... and {skipped} more chat-only personas")

    if not roster_lines:
        return

    # Build dynamic delegation rules from what specialists actually exist
    rules = _build_delegation_rules(specialists)

    injection = (
        "[Persona Agents — Your Team]\n"
        "You are a LEAD COORDINATOR. You do NOT do tasks yourself — you DELEGATE.\n"
        "ALWAYS delegate to the persona whose capabilities match the task.\n\n"
        + "\n".join(roster_lines) + "\n\n"
        "DELEGATION RULES:\n"
        + "\n".join(rules) + "\n"
        "- Call delegate_task(persona='name', task='specific task description')\n"
        "- To follow up on a completed delegate, use send_message(delegate_id='...', message='...')\n"
        "  This continues their conversation with full context — they remember everything.\n"
        "- To stop a running delegate, use cancel_delegate(delegate_id='...')\n"
        "After receiving reports, summarize findings for the user in your own voice.\n"
        "Do NOT tell the user to use tools — just give them the answer."
    )
    event.context_parts.append(injection)
    logger.info(f"Persona Agents: injected roster ({len(roster_lines)} entries):\n{injection}")
    log_prompt_inject(len(roster_lines), active_persona or "")


def _build_delegation_rules(specialist_lines):
    """Build delegation rules dynamically from what specialists actually exist."""
    rules = []

    # Parse specialist lines to see what capabilities are available
    all_caps = " ".join(specialist_lines).lower()

    if "run command" in all_caps or "scripts" in all_caps:
        rules.append("- Need commands, scripts, or code? → Delegate to a persona that 'can: run commands'")
    if "search the web" in all_caps or "research" in all_caps:
        rules.append("- Need web research or information? → Delegate to a persona that 'can: search the web'")
    if "network" in all_caps or "check internet" in all_caps or "external ip" in all_caps:
        rules.append("- Need network/system checks? → Delegate to a persona that 'can: check network health'")
    if "smart home" in all_caps or "control lights" in all_caps:
        rules.append("- Need smart home control? → Delegate to a persona that 'can: control smart home'")

    # Universal rules
    rules.append("- NEVER delegate real tasks to 'chat only' personas — they cannot execute actions")
    rules.append("- Split complex requests into separate delegations to different specialists")

    return rules


def _inject_delegate_notifications(event):
    """If any delegates have completed, tell the lead persona to retrieve results."""
    shared = _get_shared()
    if not shared:
        return

    delegates = shared._delegates
    if not delegates:
        return

    # Find completed delegates that haven't been retrieved yet
    completed = []
    for d in delegates.values():
        if d.status in ('done', 'failed') and d.result is not None:
            icon = '\u2713' if d.status == 'done' else '\u2717'
            tools = ', '.join(d.tool_log) if d.tool_log else 'none'
            completed.append(
                f"  {icon} {d.display_name} (id: {d.id}) — {d.status} in {d.elapsed}s, "
                f"tools: {tools}"
            )

    if completed:
        notification = (
            "[Delegate Report Ready]\n"
            "The following delegate(s) have finished but their results haven't been processed:\n"
            + "\n".join(completed) + "\n"
            "Call get_delegate_result(delegate_id='...') for each to read their full report.\n"
            "Then summarize the findings for the user in your own words."
        )
        event.context_parts.append(notification)
        log_event("NOTIFY", f"Injected delegate notification: {len(completed)} ready")
