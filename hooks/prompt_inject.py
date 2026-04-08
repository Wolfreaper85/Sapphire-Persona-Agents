"""
prompt_inject hook — Injects two things into the system prompt:
1. Available persona-agent roster (who can be delegated to)
2. Completed delegate notifications (so lead persona knows to retrieve results)
"""

import sys as _sys
import logging

logger = logging.getLogger(__name__)

MAX_ROSTER_ENTRIES = 10


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
    active_persona = None
    system = event.metadata.get("system")
    if system and hasattr(system, "llm_chat") and system.llm_chat:
        try:
            active_persona = system.llm_chat.session_manager.current_settings.get("persona", "")
        except Exception:
            pass

    personas = persona_manager.get_all()
    if not personas:
        return

    roster_lines = []
    for name, p in personas.items():
        # Skip the active persona (you don't delegate to yourself)
        if name == active_persona:
            continue

        settings = p.get("settings", {})
        tagline = p.get("tagline", "")
        toolset = settings.get("toolset", "conversation")

        # Get tool count
        tool_count = 0
        if toolset == "all":
            tool_count = 106
        elif toolset_manager.toolset_exists(toolset):
            tool_count = len(toolset_manager.get_toolset_functions(toolset))

        # Show key tool names so the lead knows who can do what
        key_tools = ""
        if toolset != "all" and toolset_manager.toolset_exists(toolset):
            tool_names = toolset_manager.get_toolset_functions(toolset)
            # Show up to 5 most distinctive tool names
            notable = [t for t in tool_names if t not in ('web_search', 'get_website', 'search_memory')][:5]
            if notable:
                key_tools = f" (key tools: {', '.join(notable)})"

        desc = f' — "{tagline}"' if tagline else ""
        roster_lines.append(f"  \u2022 {name}{desc} [toolset: {toolset}, {tool_count} tools]{key_tools}")

        if len(roster_lines) >= MAX_ROSTER_ENTRIES:
            roster_lines.append(f"  ... and {len(personas) - MAX_ROSTER_ENTRIES - 1} more")
            break

    if roster_lines:
        injection = (
            "[Persona Agents — Your Team]\n"
            "You can delegate tasks to these persona-agent specialists using delegate_task.\n"
            "Each has their own personality and toolset. Pick the right one for the job:\n"
            + "\n".join(roster_lines) + "\n"
            "To delegate: call delegate_task(persona='name', task='what to do').\n"
            "The tool waits for the agent to finish and returns their full report directly.\n"
            "After receiving a delegate's report, summarize the findings for the user in your\n"
            "own words and voice. Do NOT tell the user to use any tools — just give them the answer."
        )
        event.context_parts.append(injection)
        logger.debug(f"Persona Agents: injected roster ({len(roster_lines)} entries)")
        log_prompt_inject(len(roster_lines), active_persona or "")


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
