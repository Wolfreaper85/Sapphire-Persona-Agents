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
_TEAMS_FILE = Path(__file__).parent.parent.parent.parent / 'user' / 'plugin_state' / 'persona-teams.json'


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

_plugin_settings = {"auto_continue": False, "memory_mode": "auto"}

# MemPalace bridge reference (lazy-loaded)
_mp_bridge = None

def _get_mp_bridge():
    """Get MemPalace bridge module if available."""
    global _mp_bridge
    if _mp_bridge is not None:
        return _mp_bridge
    try:
        import sys
        if 'persona_agents_mempalace_bridge' in sys.modules:
            _mp_bridge = sys.modules['persona_agents_mempalace_bridge']
            return _mp_bridge
    except Exception:
        pass
    return None


async def get_settings(**kwargs):
    """Get persona-agents plugin settings."""
    result = dict(_plugin_settings)
    # Include live MemPalace detection status so UI can show it
    bridge = _get_mp_bridge()
    if bridge:
        result['mempalace_detected'] = bridge.should_use_mempalace()
        result['memory_mode'] = bridge.get_memory_mode()
    else:
        result['mempalace_detected'] = False
    return result


async def save_settings(**kwargs):
    """Save persona-agents plugin settings."""
    body = kwargs.get("body", {})
    if "auto_continue" in body:
        _plugin_settings["auto_continue"] = bool(body["auto_continue"])

    # Memory mode setting (auto/mempalace/standard/none)
    if "memory_mode" in body:
        mode = str(body["memory_mode"]).lower().strip()
        if mode in ('auto', 'mempalace', 'standard', 'none'):
            _plugin_settings["memory_mode"] = mode
            bridge = _get_mp_bridge()
            if bridge:
                bridge.set_memory_mode(mode)

    # Also update shared state so backend (prompt_inject hook) can read it
    shared = _get_shared()
    if shared:
        shared._auto_continue = _plugin_settings["auto_continue"]

    return {"success": True, **_plugin_settings}


# ── Agent Skills (per-persona skill definitions) ──────────────────────────

def _get_skills_mod():
    """Get the agent_skills module."""
    import sys
    return sys.modules.get("persona_agents_skills")


async def cancel_delegate(**kwargs):
    """Cancel a running delegate from the UI."""
    body = kwargs.get("body", {})
    delegate_id = body.get("delegate_id", "").strip()

    if not delegate_id:
        return {"error": "delegate_id required"}

    shared = _get_shared()
    if not shared:
        return {"error": "delegation system not loaded"}

    delegate = shared._delegates.get(delegate_id)
    if not delegate:
        return {"error": f"Delegate '{delegate_id}' not found"}

    if delegate.status != 'running':
        return {"error": f"Delegate already {delegate.status}"}

    delegate.cancel(force=False)
    logger.info(f"[ROUTE] Cancel delegate {delegate_id} ({delegate.display_name}) from UI")
    return {"success": True, "delegate_id": delegate_id}


async def get_skills(**kwargs):
    """Get a persona's skills.md content."""
    query = kwargs.get("query", {})
    persona = query.get("persona", "").strip().lower()

    if not persona:
        return {"error": "persona parameter required", "content": ""}

    mod = _get_skills_mod()
    if not mod:
        # Try loading directly
        try:
            from pathlib import Path
            skills_dir = Path(__file__).parent.parent.parent.parent / 'user' / 'personas' / 'skills'
            path = skills_dir / f"{persona}.md"
            content = path.read_text(encoding='utf-8').strip() if path.exists() else ""
            return {"persona": persona, "content": content}
        except Exception as e:
            return {"persona": persona, "content": "", "error": str(e)}

    content = mod.get_skills(persona)
    return {"persona": persona, "content": content}


async def save_skills(**kwargs):
    """Save a persona's skills.md content."""
    body = kwargs.get("body", {})
    persona = body.get("persona", "").strip().lower()
    content = body.get("content", "")

    if not persona:
        return {"error": "persona parameter required", "success": False}

    mod = _get_skills_mod()
    if not mod:
        # Try saving directly
        try:
            from pathlib import Path
            skills_dir = Path(__file__).parent.parent.parent.parent / 'user' / 'personas' / 'skills'
            skills_dir.mkdir(parents=True, exist_ok=True)
            path = skills_dir / f"{persona}.md"
            if content.strip():
                path.write_text(content.strip() + "\n", encoding='utf-8')
            elif path.exists():
                path.unlink()
            return {"success": True, "persona": persona}
        except Exception as e:
            return {"success": False, "error": str(e)}

    if content.strip():
        ok = mod.save_skills(persona, content)
    else:
        ok = mod.delete_skills(persona)
    return {"success": ok, "persona": persona}


async def list_skills(**kwargs):
    """List all personas that have skills files."""
    mod = _get_skills_mod()
    if not mod:
        try:
            from pathlib import Path
            skills_dir = Path(__file__).parent.parent.parent.parent / 'user' / 'personas' / 'skills'
            if not skills_dir.exists():
                return {"skills": {}}
            return {"skills": {
                p.stem: p.read_text(encoding='utf-8').strip()
                for p in skills_dir.glob("*.md") if p.is_file()
            }}
        except Exception as e:
            return {"skills": {}, "error": str(e)}

    return {"skills": mod.list_all_skills()}


# ── Teams (team management for roster filtering) ─────────────────────────

def _load_teams():
    """Load teams from disk. Returns default structure if file missing."""
    if _TEAMS_FILE.exists():
        try:
            with open(_TEAMS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load teams: {e}")
    return {
        "active_team": "all-hands",
        "teams": {
            "all-hands": {
                "name": "All Hands",
                "description": "Every persona in the system",
                "builtin": True,
                "members": {}
            }
        }
    }


def _save_teams(data):
    """Save teams to disk."""
    _TEAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_TEAMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def get_teams(**kwargs):
    """Return full teams data."""
    return _load_teams()


async def save_teams(**kwargs):
    """Save full teams data (create/edit/delete teams)."""
    body = kwargs.get("body", {})

    # Validate structure
    if "teams" not in body or "active_team" not in body:
        return {"success": False, "error": "Must include 'teams' and 'active_team'"}

    # Ensure all-hands always exists
    if "all-hands" not in body["teams"]:
        body["teams"]["all-hands"] = {
            "name": "All Hands",
            "description": "Every persona in the system",
            "builtin": True,
            "members": {}
        }

    _save_teams(body)
    return {"success": True}


async def set_active_team(**kwargs):
    """Switch the active team (fast path for dropdown)."""
    body = kwargs.get("body", {})
    team_key = body.get("team", "").strip()

    if not team_key:
        return {"success": False, "error": "team parameter required"}

    data = _load_teams()

    if team_key not in data["teams"]:
        return {"success": False, "error": f"Team '{team_key}' not found"}

    data["active_team"] = team_key
    _save_teams(data)
    logger.info(f"[PERSONA-AGENTS] Active team set to: {team_key}")
    return {"success": True, "active_team": team_key}


async def toggle_team_member(**kwargs):
    """Toggle a single persona on/off within a team."""
    body = kwargs.get("body", {})
    team_key = body.get("team", "").strip()
    persona = body.get("persona", "").strip()
    enabled = body.get("enabled", True)

    if not team_key or not persona:
        return {"success": False, "error": "team and persona parameters required"}

    data = _load_teams()
    team = data["teams"].get(team_key)

    if not team:
        return {"success": False, "error": f"Team '{team_key}' not found"}

    if team_key == "all-hands":
        return {"success": False, "error": "Cannot toggle members in All Hands"}

    team["members"][persona] = bool(enabled)
    _save_teams(data)
    return {"success": True, "team": team_key, "persona": persona, "enabled": bool(enabled)}
