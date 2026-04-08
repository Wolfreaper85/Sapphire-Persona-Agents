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
    'web_search', 'get_website', 'get_youtube_transcript', 'get_wikipedia',
    'research_topic', 'get_site_links', 'get_images',
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
    # Shared context
    'shared_context_write', 'shared_context_read',
    # Sub-delegation
    'sub_delegate',
    # Agent learning
    'record_lesson', 'contradict_lesson',
}

# ── Task-keyword → tool mapping ────────────────────────────────────────────
# When the user's message contains these keywords, the corresponding tools
# are what's ACTUALLY needed to fulfill the request. We score personas by
# how many of the needed tools they have — matching happens in CODE, not prompt.
#
# This is the Paperclip approach: analyze the task, score candidates, recommend.

_TASK_SIGNALS = {
    # keyword/phrase → set of tools that would be needed
    # ── Research / Web ──
    'search':       {'web_search', 'research_topic'},
    'research':     {'web_search', 'research_topic', 'get_wikipedia'},
    'look up':      {'web_search', 'get_website'},
    'find out':     {'web_search', 'research_topic'},
    'google':       {'web_search'},
    'summarize':    {'web_search', 'get_website', 'research_topic'},
    'summary':      {'web_search', 'get_website', 'research_topic'},
    'break down':   {'web_search', 'get_website', 'research_topic'},
    'breakdown':    {'web_search', 'get_website', 'research_topic'},
    'explain':      {'web_search', 'get_website', 'research_topic'},
    'article':      {'web_search', 'get_website'},
    'video':        {'get_youtube_transcript', 'web_search', 'get_website'},
    'youtube':      {'get_youtube_transcript', 'web_search', 'get_website'},
    'news':         {'web_search', 'research_topic'},
    'headline':     {'web_search', 'research_topic'},
    'weather':      {'web_search'},
    'price':        {'web_search'},
    'stock':        {'web_search'},
    'review':       {'web_search', 'get_website', 'research_topic'},
    'compare':      {'web_search', 'research_topic'},
    'what is':      {'web_search', 'get_wikipedia'},
    'who is':       {'web_search', 'get_wikipedia'},
    'how to':       {'web_search', 'get_website'},
    'tutorial':     {'web_search', 'get_website'},
    'recipe':       {'web_search', 'get_website'},
    'website':      {'get_website', 'get_site_links'},
    'wikipedia':    {'get_wikipedia'},
    'browse':       {'tandem_browse', 'tandem_read_page'},
    # ── Commands / Engineering ──
    'run':          {'run_command'},
    'execute':      {'run_command', 'execute_code'},
    'install':      {'run_command'},
    'script':       {'run_command'},
    'command':      {'run_command'},
    'code':         {'run_command', 'execute_code', 'ask_claude'},
    'build':        {'run_command', 'ask_claude'},
    'fix':          {'run_command', 'ask_claude'},
    'debug':        {'run_command', 'ask_claude'},
    'compile':      {'run_command'},
    'deploy':       {'run_command'},
    'pip':          {'run_command'},
    'npm':          {'run_command'},
    'git':          {'run_command'},
    'docker':       {'run_command'},
    'update':       {'run_command'},
    'restart':      {'run_command'},
    'backup':       {'run_command'},
    'download':     {'run_command'},
    'create file':  {'run_command'},
    'write file':   {'run_command'},
    # ── Network / System ──
    'network':      {'check_internet', 'get_external_ip', 'website_status'},
    'ping':         {'run_command', 'check_internet'},
    'dns':          {'run_command', 'check_internet'},
    'server':       {'run_command', 'check_internet', 'website_status'},
    'port':         {'run_command'},
    'ip':           {'get_external_ip', 'check_internet'},
    'uptime':       {'website_status', 'check_internet'},
    'status':       {'website_status', 'check_internet'},
    'connectivity': {'check_internet', 'website_status'},
    # ── Smart Home ──
    'light':        {'ha_set_light', 'ha_activate'},
    'lights':       {'ha_set_light', 'ha_activate'},
    'thermostat':   {'ha_set_thermostat'},
    'temperature':  {'ha_set_thermostat'},
    'smart home':   {'ha_activate', 'ha_set_light', 'ha_set_thermostat'},
    'home assistant': {'ha_activate', 'ha_list_areas'},
    # ── Other ──
    'image':        {'get_images'},
    'picture':      {'get_images'},
    'photo':        {'get_images'},
    'persona':      {'create_full_persona', 'research_character'},
}

# Capability categories — detected from which functions a toolset has
_CAPABILITY_MAP = {
    'run_command':       'run commands & scripts',
    'execute_code':      'execute code',
    'web_search':        'search the web',
    'research_topic':    'research topics in depth',
    'get_website':       'read websites',
    'get_youtube_transcript': 'read YouTube video transcripts',
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


def _score_persona_for_task(tool_names, user_message):
    """Score how well a persona's tools match what the user's message actually needs.

    Returns (score, needed_tools_matched, total_needed).
    Score 0 = no match. Higher = better fit.

    This is the Paperclip approach: analyze the task in CODE, don't leave it
    to the LLM to pattern-match against a roster list.
    """
    if not user_message:
        # No message context — fall back to raw capability count
        return len(set(tool_names) & _TASK_FUNCTIONS), set(), set()

    msg_lower = user_message.lower()

    # Collect all tools that the task signals suggest are needed
    needed_tools = set()
    for keyword, tools in _TASK_SIGNALS.items():
        if keyword in msg_lower:
            needed_tools.update(tools)

    if not needed_tools:
        # No signal keywords found — fall back to raw capability count
        return len(set(tool_names) & _TASK_FUNCTIONS), set(), set()

    # Score = how many of the needed tools does this persona have?
    tool_set = set(tool_names)
    matched = tool_set & needed_tools

    # Weighted score: matched tools + small bonus for total capability breadth
    score = len(matched) * 10 + len(tool_set & _TASK_FUNCTIONS)

    return score, matched, needed_tools


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
    """Inject the available persona-agent roster, scored against the user's actual message.

    Paperclip approach: analyze the task in code, score each persona by how well
    their tools match what the task actually needs, present a ranked roster with
    a recommended pick. The LLM doesn't have to figure out who to send — we tell it.
    """
    from core.personas.persona_manager import persona_manager
    from core.toolsets import toolset_manager

    # Get current active persona so we don't list ourselves
    active_persona = None
    active_tools = []
    user_message = ""
    try:
        from core.api_fastapi import get_system
        sys_obj = get_system()
        if sys_obj and hasattr(sys_obj, 'llm_chat') and sys_obj.llm_chat:
            settings = sys_obj.llm_chat.session_manager.current_settings
            active_persona = settings.get("persona", "")
            active_toolset = settings.get("toolset", "")
            if active_toolset and toolset_manager.toolset_exists(active_toolset):
                active_tools = toolset_manager.get_toolset_functions(active_toolset)
            # Grab the user's latest message for task analysis
            try:
                user_message = event.metadata.get('user_message', '') or ''
            except Exception:
                pass
            logger.debug(f"Active persona={active_persona!r}, toolset={active_toolset!r}, "
                        f"tools={len(active_tools)}, user_msg_len={len(user_message)}")
        else:
            logger.debug("No system/llm_chat available")
    except Exception as e:
        logger.debug(f"Active persona detection failed: {e}")

    personas = persona_manager.get_all()
    if not personas:
        logger.debug("No personas found, skipping roster")
        return

    # Only inject if the active persona is a coordinator (has delegate_task)
    if active_tools and not _is_coordinator(active_tools):
        logger.debug(f"Skipping roster — {active_persona!r} is not a coordinator")
        return

    # ── Score every persona against the user's message ──────────────────────
    specialists = []   # (score, name, line, matched_tools)
    chat_only = []

    for name, p in personas.items():
        if not isinstance(p, dict):
            continue
        if name == active_persona:
            continue

        settings = p.get("settings", {})
        tagline = p.get("tagline", "")
        toolset = settings.get("toolset", "conversation")

        tool_names = []
        if toolset == "all":
            tool_names = ['run_command', 'web_search', 'get_website']
        elif toolset_manager.toolset_exists(toolset):
            tool_names = toolset_manager.get_toolset_functions(toolset)

        tool_count = len(tool_names) if toolset != "all" else 106

        if _is_task_capable(tool_names):
            caps = _detect_capabilities(tool_names)
            score, matched, needed = _score_persona_for_task(tool_names, user_message)
            cap_str = f" — can: {', '.join(caps[:5])}" if caps else ""
            line = f"  - {name} [{toolset}, {tool_count} tools]{cap_str}"
            specialists.append((score, name, line, matched))
        else:
            desc = f' — "{tagline}"' if tagline else ""
            line = f"  - {name}{desc} [chat only, no task tools]"
            chat_only.append(line)

    # Sort by score descending — best match for THIS task at the top
    specialists.sort(key=lambda x: x[0], reverse=True)

    # Debug: log the scoring so we can verify task matching
    if specialists:
        score_summary = ", ".join(f"{name}={score}" for score, name, _, _ in specialists[:6])
        logger.info(f"[TASK-SCORING] msg='{user_message[:80]}' → {score_summary}")

    # Build the roster lines
    specialist_lines = []
    best_pick = None
    for i, (score, name, line, matched) in enumerate(specialists):
        if i == 0 and score > 0 and matched:
            # Top scorer with actual tool matches = recommended pick
            best_pick = name
            line += f"  ⭐ BEST MATCH"
        specialist_lines.append(line)

    roster_lines = specialist_lines[:MAX_ROSTER_ENTRIES]
    remaining_slots = MAX_ROSTER_ENTRIES - len(roster_lines)
    if remaining_slots > 0 and chat_only:
        roster_lines.extend(chat_only[:remaining_slots])

    skipped = len(specialist_lines) + len(chat_only) - len(roster_lines)
    if skipped > 0:
        roster_lines.append(f"  ... and {skipped} more chat-only personas")

    if not roster_lines:
        return

    rules = _build_delegation_rules(specialist_lines)

    # Build the recommendation line
    if best_pick:
        recommend = (
            f"\n⭐ RECOMMENDED: For this task, delegate to '{best_pick}' — "
            f"they have the best tool match for what the user is asking.\n"
        )
    else:
        recommend = ""

    injection = (
        "[Persona Agents — Your Team]\n"
        "You are a LEAD COORDINATOR. You do NOT do tasks yourself — you DELEGATE.\n"
        "ALWAYS delegate to the persona whose tools BEST match the task.\n"
        "The roster below is SORTED by relevance to the current request — prefer the top entries.\n\n"
        + "\n".join(roster_lines) + "\n"
        + recommend + "\n"
        "DELEGATION RULES:\n"
        + "\n".join(rules) + "\n"
        "- Call delegate_task(persona='name', task='specific task description')\n"
        "- To follow up on a completed delegate, use send_message(delegate_id='...', message='...')\n"
        "  This continues their conversation with full context — they remember everything.\n"
        "- To stop a running delegate, use cancel_delegate(delegate_id='...')\n"
        "- Agents share a team scratchpad — findings are automatically visible to new delegates.\n"
        "- TASK DECOMPOSITION: If the user's request has multiple DISTINCT parts that need "
        "different skills (e.g. 'check the server AND research X'), split them into SEPARATE "
        "delegations to DIFFERENT specialists. Do NOT dump everything on one persona.\n"
        "  Example: 'check if servers are up and find news about NVIDIA' → "
        "delegate network check to sysadmin, delegate research to researcher.\n"
        "After receiving reports, summarize findings for the user in your own voice.\n"
        "Do NOT tell the user to use tools — just give them the answer."
    )
    event.context_parts.append(injection)
    if best_pick:
        logger.info(f"Persona Agents: injected roster ({len(roster_lines)} entries), "
                    f"recommended={best_pick}")
    else:
        logger.info(f"Persona Agents: injected roster ({len(roster_lines)} entries), no strong recommendation")
    log_prompt_inject(len(roster_lines), active_persona or "")


def _build_delegation_rules(specialist_lines):
    """Build delegation rules dynamically from what specialists actually exist."""
    rules = []

    # Parse specialist lines to see what capabilities are available
    all_caps = " ".join(specialist_lines).lower()

    if "run command" in all_caps:
        rules.append("- Commands, scripts, installs, system work? → Pick the specialist with 'run commands' that's ranked highest")
    if "search the web" in all_caps or "research" in all_caps:
        rules.append("- Research, lookups, fact-checking? → Pick the specialist with 'search the web' that's ranked highest")
    if "check network" in all_caps or "external ip" in all_caps:
        rules.append("- Network diagnostics, uptime, connectivity? → Pick the specialist with network tools ranked highest")
    if "smart home" in all_caps or "control lights" in all_caps:
        rules.append("- Smart home, lights, thermostat? → Pick the specialist with 'control smart home'")

    # Universal rules
    rules.append("- The roster is SORTED by match quality — the ⭐ BEST MATCH persona (if shown) is your first choice")
    rules.append("- NEVER delegate real tasks to 'chat only' personas — they cannot execute actions")
    rules.append("- If no ⭐ is shown, pick the top specialist whose capabilities fit the request")

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
