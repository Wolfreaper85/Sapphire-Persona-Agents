# mempalace_bridge.py — IFTTT bridge between Persona Agents and MemPalace
#
# Auto-detects if MemPalace plugin is installed and enabled.
# When available: injects memory layers into delegate prompts and
# swaps old memory tools for MemPalace equivalents at runtime.
# When unavailable: everything works exactly as before.
#
# Zero changes to MemPalace — all logic lives here in persona-agents.

import logging

logger = logging.getLogger(__name__)

# ── Tool swap mapping ────────────────────────────────────────────────────────
# Old memory/knowledge tools → MemPalace equivalents.
# Tools with no equivalent (delete_memory, delete_knowledge) are dropped
# when MemPalace is active — the palace handles its own cleanup.

_OLD_TO_MEMPALACE = {
    'save_memory':        'memory_remember',
    'search_memory':      'memory_search',
    'get_recent_memories': 'memory_recall',
    'save_knowledge':     'memory_remember',   # Knowledge merges into palace
    'search_knowledge':   'memory_search',
    'delete_memory':      None,                # No equivalent — drop
    'delete_knowledge':   None,                # No equivalent — drop
    'recall_memory':      'memory_recall',     # Alias some systems use
}

# The full set of MemPalace tool names we might inject
_MEMPALACE_TOOLS = {'memory_remember', 'memory_recall', 'memory_search', 'memory_diary'}

# Old tools that get replaced (all keys from the mapping)
_OLD_MEMORY_TOOLS = set(_OLD_TO_MEMPALACE.keys())

# ── Detection cache ──────────────────────────────────────────────────────────
# Cached per-session so we don't re-check on every delegation.

_cache = {
    'checked': False,
    'available': False,
    'retrieval': None,
}


def _check_mempalace():
    """Check if MemPalace plugin is installed, enabled, and functional."""
    if _cache['checked']:
        return _cache['available']

    _cache['checked'] = True

    try:
        # Step 1: Check if plugin is loaded and enabled
        from core.plugin_loader import plugin_loader
        enabled = plugin_loader.get_enabled_plugins()
        if 'mempalace' not in enabled:
            logger.info("[MEMPALACE-BRIDGE] MemPalace plugin not enabled")
            _cache['available'] = False
            return False

        # Step 2: Check if retrieval module is importable
        from plugins.mempalace.lib import retrieval
        _cache['retrieval'] = retrieval
        _cache['available'] = True
        logger.info("[MEMPALACE-BRIDGE] MemPalace detected and available")
        return True

    except ImportError:
        logger.info("[MEMPALACE-BRIDGE] MemPalace retrieval module not importable")
        _cache['available'] = False
        return False
    except Exception as e:
        logger.warning(f"[MEMPALACE-BRIDGE] Detection error: {e}")
        _cache['available'] = False
        return False


def reset_cache():
    """Reset detection cache (e.g. after plugin enable/disable)."""
    _cache['checked'] = False
    _cache['available'] = False
    _cache['retrieval'] = None


# ── Memory mode setting ──────────────────────────────────────────────────────
# 'auto'     — detect MemPalace, use if available (default)
# 'mempalace' — force MemPalace tools (error if not installed)
# 'standard' — force old memory tools regardless
# 'none'     — no memory tools at all

_memory_mode = 'auto'


def get_memory_mode():
    return _memory_mode


def set_memory_mode(mode):
    global _memory_mode
    if mode in ('auto', 'mempalace', 'standard', 'none'):
        _memory_mode = mode
        # Reset cache when mode changes so next delegation re-evaluates
        reset_cache()
        logger.info(f"[MEMPALACE-BRIDGE] Memory mode set to: {mode}")
    else:
        logger.warning(f"[MEMPALACE-BRIDGE] Invalid memory mode: {mode}")


def should_use_mempalace():
    """Determine if MemPalace should be used based on mode + detection."""
    mode = _memory_mode

    if mode == 'none':
        return False
    if mode == 'standard':
        return False
    if mode == 'mempalace':
        return True  # Force on — caller should handle missing module
    # mode == 'auto'
    return _check_mempalace()


# ── Memory injection (for delegate system prompts) ───────────────────────────

def get_memory_injection(persona_name, task_text=''):
    """
    Get MemPalace memory layers for a persona to inject into a delegate's prompt.

    Returns a string block to append to the system prompt, or '' if unavailable.
    Called from PersonaDelegate._execute() AFTER ExecutionContext is created.

    Args:
        persona_name: The delegate's persona name (used as wing key)
        task_text: The task being delegated (used for L2 relevance matching)
    """
    if not should_use_mempalace():
        return ''

    retrieval = _cache.get('retrieval')
    if not retrieval:
        # Try one more time if forced mode
        try:
            from plugins.mempalace.lib import retrieval as r
            retrieval = r
        except Exception:
            return ''

    try:
        persona_key = persona_name.lower().strip()
        parts = []

        # L0 — Identity (always)
        l0 = retrieval.get_l0(persona_key)
        if l0:
            parts.append(l0)

        # L1 — Essential Knowledge (always)
        l1 = retrieval.get_l1(persona_key, max_tokens=600)
        if l1:
            parts.append(l1)

        # L2 — On-Demand Context (when task text provides relevance signal)
        if task_text:
            l2 = retrieval.get_l2(persona_key, task_text, max_tokens=350, threshold=0.35)
            if l2:
                parts.append(l2)

        if parts:
            header = "\n\n[MemPalace — Your Memories]\n"
            block = header + "\n".join(parts)
            logger.info(f"[MEMPALACE-BRIDGE] Injected {len(parts)} memory layers for {persona_key}")
            return block

    except Exception as e:
        logger.warning(f"[MEMPALACE-BRIDGE] Memory injection error for {persona_name}: {e}")

    return ''


# ── Toolset swap (for delegate tool lists) ───────────────────────────────────

def swap_tools_in_list(tool_names):
    """
    Given a list of tool function names, swap old memory/knowledge tools
    for MemPalace equivalents if MemPalace is active.

    Returns a new list (does not mutate the original).
    If MemPalace is not active, returns the original list unchanged.

    Args:
        tool_names: list of str — function names from a toolset
    Returns:
        list of str — potentially modified function names
    """
    if not should_use_mempalace():
        return tool_names

    result = []
    added_mp = set()  # Track which MemPalace tools we've already added (avoid dupes)

    for name in tool_names:
        if name in _OLD_MEMORY_TOOLS:
            replacement = _OLD_TO_MEMPALACE.get(name)
            if replacement and replacement not in added_mp:
                result.append(replacement)
                added_mp.add(replacement)
            # If replacement is None, the tool is simply dropped
        else:
            result.append(name)

    # Always add memory_diary if we swapped anything — it's a MemPalace
    # unique tool (personal journal entries) with no old equivalent
    if added_mp and 'memory_diary' not in added_mp:
        result.append('memory_diary')

    if added_mp:
        logger.debug(f"[MEMPALACE-BRIDGE] Swapped tools: removed {_OLD_MEMORY_TOOLS & set(tool_names)}, "
                      f"added {added_mp | {'memory_diary'}}")

    return result


def swap_tools_in_ctx(ctx):
    """
    Swap old memory tools for MemPalace tools in an ExecutionContext's
    resolved tool list. Modifies ctx.tools and ctx._allowed_tool_names in place.

    Called from PersonaDelegate._execute() AFTER ExecutionContext is created.

    Args:
        ctx: ExecutionContext instance with .tools and ._allowed_tool_names
    """
    if not should_use_mempalace():
        return

    if not ctx.tools:
        return

    old_names = _OLD_MEMORY_TOOLS
    mp_names_needed = set()

    # Figure out which MemPalace tools we need
    for tool in ctx.tools:
        fn_name = tool.get('function', {}).get('name', '')
        if fn_name in old_names:
            replacement = _OLD_TO_MEMPALACE.get(fn_name)
            if replacement:
                mp_names_needed.add(replacement)

    if not mp_names_needed:
        return  # No old memory tools in this toolset, nothing to swap

    # Always include memory_diary as bonus
    mp_names_needed.add('memory_diary')

    # Remove old memory tools from the list
    ctx.tools = [t for t in ctx.tools
                 if t.get('function', {}).get('name', '') not in old_names]

    # Find MemPalace tool definitions from all_possible_tools
    for tool in ctx.fm.all_possible_tools:
        fn_name = tool.get('function', {}).get('name', '')
        if fn_name in mp_names_needed:
            # Don't add duplicates
            existing = {t.get('function', {}).get('name', '') for t in ctx.tools}
            if fn_name not in existing:
                ctx.tools.append(tool)

    # Rebuild allowed names set
    ctx._allowed_tool_names = {
        t['function']['name'] for t in ctx.tools if 'function' in t
    } if ctx.tools else None

    logger.info(f"[MEMPALACE-BRIDGE] Swapped delegate tools: added {mp_names_needed}")
