"""
pre_chat hook — Dynamic delegation injection.

Any active persona automatically gets delegation tools (delegate_task,
check_delegates, get_delegate_result, cancel_delegate, send_message)
injected into their enabled tool list. This makes every persona a
potential coordinator without permanently modifying their toolset.

Only fires for the main chat loop — delegated agents running in
ExecutionContext are NOT affected (they don't trigger pre_chat).
"""

import logging

logger = logging.getLogger(__name__)

# The delegation tools that get injected into the active persona
_DELEGATION_TOOLS = {
    'delegate_task',
    'check_delegates',
    'get_delegate_result',
    'cancel_delegate',
    'send_message',
}


def pre_chat(event):
    """Inject delegation tools into the active persona's enabled tool list."""
    try:
        system = event.metadata.get("system")
        if not system or not hasattr(system, "llm_chat") or not system.llm_chat:
            return

        fm = system.llm_chat.function_manager

        # Check if delegation tools are already enabled (e.g. coordinator toolset)
        enabled_names = {t['function']['name'] for t in fm._enabled_tools if 'function' in t}
        if 'delegate_task' in enabled_names:
            logger.debug("[DYNAMIC-DELEGATE] Already has delegate_task, skipping injection")
            return

        # Find delegation tools from all_possible_tools
        tools_to_add = []
        for tool in fm.all_possible_tools:
            name = tool.get('function', {}).get('name', '')
            if name in _DELEGATION_TOOLS and name not in enabled_names:
                tools_to_add.append(tool)

        if not tools_to_add:
            logger.debug("[DYNAMIC-DELEGATE] No delegation tools found in all_possible_tools")
            return

        # Inject them into the enabled list
        fm._enabled_tools.extend(tools_to_add)

        added_names = [t['function']['name'] for t in tools_to_add]
        logger.info(f"[DYNAMIC-DELEGATE] Injected {len(tools_to_add)} delegation tools: {added_names}")

    except Exception as e:
        logger.error(f"[DYNAMIC-DELEGATE] pre_chat error: {e}")
