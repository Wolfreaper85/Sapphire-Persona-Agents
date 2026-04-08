# routes/delegation.py — API for the visual delegation panel
# Serves persona list and session transcript for the Round Table-style UI
#
# NOTE: Plugin routes are exec()'d, not imported. Use sys.modules-registered
# shared state modules instead of "from plugins.persona_agents..." imports.

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent.parent.parent.parent / 'user' / 'plugin_state' / 'persona-agents.json'


def _persist_sessions(shared):
    """Save sessions to disk after changes."""
    try:
        if shared and hasattr(shared, '_sessions'):
            _STATE_FILE.write_text(
                json.dumps({'sessions': dict(shared._sessions)}, default=str),
                encoding='utf-8',
            )
    except Exception as e:
        logger.debug(f"Failed to persist sessions: {e}")


def _get_shared():
    """Get shared state (delegates, sessions) registered by the tool module."""
    import sys
    return sys.modules.get("persona_agents_shared")


def _get_dlog():
    """Get delegation log module registered by the tool module."""
    import sys
    return sys.modules.get("persona_agents_delegation_log")


async def get_available_personas(**kwargs):
    """Get all personas available for delegation with their toolset info."""
    try:
        from core.personas.persona_manager import persona_manager
        from core.toolsets import toolset_manager

        personas = persona_manager.get_all()
        result = []
        for name, p in personas.items():
            settings = p.get("settings", {})
            toolset = settings.get("toolset", "conversation")
            # Get tool count for this toolset
            tool_count = 0
            if toolset == "all":
                tool_count = 106
            elif toolset_manager.toolset_exists(toolset):
                tool_count = len(toolset_manager.get_toolset_functions(toolset))

            result.append({
                "name": name,
                "display_name": p.get("name", name),
                "tagline": p.get("tagline", ""),
                "avatar": p.get("avatar"),
                "trim_color": settings.get("trim_color", "#4a9eff"),
                "toolset": toolset,
                "tool_count": tool_count,
                "voice": settings.get("voice", ""),
            })
        return {"personas": result}
    except Exception as e:
        logger.error(f"get_available_personas error: {e}")
        return {"personas": [], "error": str(e)}


async def list_sessions(**kwargs):
    """List active delegation sessions."""
    shared = _get_shared()
    if not shared:
        return {"sessions": []}

    result = []
    for chat_name, session in shared._sessions.items():
        result.append({
            "id": session["id"],
            "chat_name": chat_name,
            "message_count": len(session["transcript"]),
            "created_at": session.get("created_at", ""),
        })
    return {"sessions": result}


async def get_session(**kwargs):
    """Get full session transcript for the visual panel."""
    query = kwargs.get("query", {})
    chat_name = query.get("chat_name", "")

    if not chat_name:
        # Try to get active chat
        try:
            from core.api_fastapi import get_system
            chat_name = get_system().llm_chat.get_active_chat() or 'default'
        except Exception:
            chat_name = 'default'

    shared = _get_shared()
    if not shared:
        return {"transcript": [], "active_delegates": []}

    session = shared._sessions.get(chat_name)
    if not session:
        return {"transcript": [], "active_delegates": []}

    # Include active delegate status
    active = []
    for d in shared._delegates.values():
        if d.chat_name == chat_name:
            active.append(d.to_dict())

    return {
        "transcript": session["transcript"],
        "active_delegates": active,
    }


async def clear_session(**kwargs):
    """Clear the delegation transcript for a chat."""
    body = kwargs.get("body", {})
    chat_name = body.get("chat_name", "")

    if not chat_name:
        try:
            from core.api_fastapi import get_system
            chat_name = get_system().llm_chat.get_active_chat() or 'default'
        except Exception:
            chat_name = 'default'

    shared = _get_shared()
    if shared:
        shared._sessions.pop(chat_name, None)
        _persist_sessions(shared)
    return {"success": True}


async def get_delegation_log(**kwargs):
    """Return the last N lines of the persona agents log file."""
    query = kwargs.get("query", {})
    lines = int(query.get("lines", 150))
    lines = min(lines, 500)  # Cap at 500

    try:
        dlog = _get_dlog()
        if not dlog:
            return {"log": "(delegation log module not loaded yet)", "lines_requested": lines}
        content = dlog.read_log(lines)
        return {"log": content, "lines_requested": lines}
    except Exception as e:
        logger.error(f"get_delegation_log error: {e}")
        return {"log": f"(error: {e})", "lines_requested": lines}


async def get_log_stats(**kwargs):
    """Return log file stats (size, modified date, etc.)."""
    try:
        dlog = _get_dlog()
        if not dlog:
            return {"exists": False, "error": "log module not loaded"}
        return dlog.get_log_stats()
    except Exception as e:
        return {"exists": False, "error": str(e)}


# ── Plugin Settings (auto-continue toggle, etc.) ────────────────────────────

_plugin_settings = {"auto_continue": False}


async def get_settings(**kwargs):
    """Get persona-agents plugin settings."""
    return dict(_plugin_settings)


async def save_settings(**kwargs):
    """Save persona-agents plugin settings."""
    body = kwargs.get("body", {})
    if "auto_continue" in body:
        _plugin_settings["auto_continue"] = bool(body["auto_continue"])

    # Also update shared state so backend (prompt_inject hook) can read it
    shared = _get_shared()
    if shared:
        shared._auto_continue = _plugin_settings["auto_continue"]

    return {"success": True, **_plugin_settings}
