# Persona Agents — delegate tasks to persona-powered specialists
# Each agent runs with their persona's full prompt + a focused toolset
# Results include character flair (in-character intro/outro)

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Dedicated delegation log (auto-pruning file in user/logs/)
# Plugin tools are exec()'d, not imported — use importlib path-based loading
# Also register in sys.modules so routes/hooks can access via normal imports
import sys as _sys
import importlib.util as _ilu

def _load_sibling_module(filename, module_name):
    """Load a Python file from this plugin's directory and register in sys.modules."""
    path = str(Path(__file__).parent.parent / filename)
    spec = _ilu.spec_from_file_location(module_name, path)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod

try:
    _skills_mod = _load_sibling_module("agent_skills.py", "persona_agents_skills")
    from persona_agents_skills import get_skills_for_prompt as _skills_get_prompt
    _has_skills = True
except Exception as _se:
    logger.warning(f"[PERSONA-AGENT] Agent skills module unavailable: {_se}")
    _has_skills = False
    def _skills_get_prompt(*a, **kw): return ""

try:
    _lessons_mod = _load_sibling_module("agent_lessons.py", "persona_agents_lessons")
    from persona_agents_lessons import record_lesson as _lessons_record
    from persona_agents_lessons import contradict_lesson as _lessons_contradict
    from persona_agents_lessons import get_lessons_for_prompt as _lessons_get_prompt
    _has_lessons = True
except Exception as _le:
    logger.warning(f"[PERSONA-AGENT] Agent lessons module unavailable: {_le}")
    _has_lessons = False
    def _lessons_record(*a, **kw): pass
    def _lessons_contradict(*a, **kw): pass
    def _lessons_get_prompt(*a, **kw): return ""

try:
    _dlog_mod = _load_sibling_module("delegation_log.py", "persona_agents_delegation_log")
    log_dispatch = _dlog_mod.log_dispatch
    log_tool_call = _dlog_mod.log_tool_call
    log_result = _dlog_mod.log_result
    log_batch_complete = _dlog_mod.log_batch_complete
    log_event = _dlog_mod.log_event
    _has_dlog = True
except Exception as _e:
    logger.warning(f"[PERSONA-AGENT] Delegation log unavailable: {_e}")
    _has_dlog = False
    def log_dispatch(*a, **kw): pass
    def log_tool_call(*a, **kw): pass
    def log_result(*a, **kw): pass
    def log_batch_complete(*a, **kw): pass
    def log_event(*a, **kw): pass

# MemPalace IFTTT bridge — auto-detects and integrates if mempalace plugin is present
try:
    _mp_bridge = _load_sibling_module("mempalace_bridge.py", "persona_agents_mempalace_bridge")
    _has_mempalace_bridge = True
except Exception as _mpe:
    logger.info(f"[PERSONA-AGENT] MemPalace bridge unavailable: {_mpe}")
    _has_mempalace_bridge = False
    _mp_bridge = None

ENABLED = True
EMOJI = '\U0001f3ad'


class _DelegateCancelled(Exception):
    """Raised when a delegate is cancelled mid-execution."""
    pass

# ── Monkey-patch ExecutionContext.run() ──────────────────────────────────────
# Core's run() calls filter_to_thinking_only() on tool-call rounds, which
# strips all real content and keeps only <think> blocks. The streaming chat
# does NOT do this — it preserves full content. This patch aligns run()
# with the streaming behavior so delegate results aren't lost.
try:
    from core.continuity.execution_context import ExecutionContext as _EC
    import config as _ec_config
    from typing import List, Dict
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    _original_run = _EC.run  # Keep reference for non-delegate usage
    _LLM_CALL_TIMEOUT = 120  # seconds — max time for a single LLM call before we bail

    def _patched_run(self, user_input: str, history_messages: List[Dict] = None) -> str:
        """Patched run() that preserves content on tool-call rounds for delegates.
        Non-delegate calls pass through to original run() unchanged."""
        # Only apply fix when called from a delegate (marked by _persona_agent flag)
        if not getattr(self, '_persona_agent', False):
            return _original_run(self, user_input, history_messages)

        from core.chat.chat import _inject_tool_images

        if history_messages is not None:
            messages = [{"role": "system", "content": self.system_prompt}] + history_messages
            messages.append({"role": "user", "content": user_input})
        else:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_input}
            ]

        max_iterations = self.task_settings.get("max_tool_rounds") or _ec_config.MAX_TOOL_ITERATIONS
        max_parallel = self.task_settings.get("max_parallel_tools") or _ec_config.MAX_PARALLEL_TOOLS
        context_limit = self.task_settings.get("context_limit") or getattr(_ec_config, 'CONTEXT_LIMIT', 0)

        final_content = None
        _cancel = getattr(self, '_cancel_event', None)
        _force = getattr(self, '_force_cancel_event', None)

        for i in range(max_iterations):
            # Check cancellation between iterations
            if _force and _force.is_set():
                raise _DelegateCancelled()
            if _cancel and _cancel.is_set():
                break  # Graceful — stop after current iteration, return what we have

            if context_limit > 0:
                from core.chat.history import count_tokens
                total_tokens = sum(count_tokens(str(m.get("content", ""))) for m in messages)
                if total_tokens > context_limit * 0.9:
                    break

            # Wrap LLM call in a timeout so a hung provider can't freeze the delegate forever.
            # Poll in 2s increments so cancel events are checked while waiting.
            # NOTE: We avoid the `with` context manager because shutdown(wait=True)
            # would block until the thread finishes, defeating the timeout entirely.
            _pool = ThreadPoolExecutor(max_workers=1)
            _llm_bail = False
            try:
                _future = _pool.submit(
                    self.tool_engine.call_llm_with_metrics,
                    self.provider, messages, self.gen_params, tools=self.tools
                )
                _elapsed = 0
                while _elapsed < _LLM_CALL_TIMEOUT:
                    try:
                        response_msg = _future.result(timeout=2)
                        break  # Got a result
                    except FuturesTimeout:
                        _elapsed += 2
                        # Check cancel while waiting for LLM
                        if _force and _force.is_set():
                            raise _DelegateCancelled()
                        if _cancel and _cancel.is_set():
                            # Soft cancel — break gracefully (matches behavior between iterations)
                            _llm_bail = True
                            break
                else:
                    # Exhausted timeout
                    logger.warning(f"[PERSONA-AGENT] LLM call timed out after {_LLM_CALL_TIMEOUT}s on round {i+1}")
                    _llm_bail = True
            except _DelegateCancelled:
                # Fire-and-forget cleanup — don't wait for the orphaned thread
                _pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as _llm_err:
                logger.error(f"[PERSONA-AGENT] LLM call failed on round {i+1}: {_llm_err}")
                _llm_bail = True
            finally:
                # Non-blocking cleanup — orphaned thread will finish on its own
                _pool.shutdown(wait=False, cancel_futures=True)

            if _llm_bail:
                break

            if response_msg.has_tool_calls:
                # KEY FIX: preserve full content instead of filter_to_thinking_only
                content = response_msg.content or ""
                tool_calls = response_msg.get_tool_calls_as_dicts()[:max_parallel]
                messages.append({
                    "role": "assistant", "content": content,
                    "tool_calls": tool_calls
                })
                # Track the content in case this is the last round
                if content.strip():
                    final_content = content
                self.tool_log.extend(tc.get('function', {}).get('name', '?') for tc in tool_calls)

                # Check force cancel before executing tools
                if _force and _force.is_set():
                    raise _DelegateCancelled()

                tools_executed, tool_images = self.tool_engine.execute_tool_calls(
                    tool_calls, messages, None, self.provider, scopes=self.scopes,
                    allowed_tools=self._allowed_tool_names
                )
                if tool_images:
                    _inject_tool_images(messages, tool_images)
                continue

            elif response_msg.content:
                fn_data = self.tool_engine.extract_function_call_from_text(response_msg.content)
                if fn_data:
                    self.tool_log.append(fn_data.get('name', '?'))
                    content = response_msg.content
                    _, tool_images = self.tool_engine.execute_text_based_tool_call(
                        fn_data, content, messages, None, self.provider, scopes=self.scopes,
                        allowed_tools=self._allowed_tool_names
                    )
                    if tool_images:
                        _inject_tool_images(messages, tool_images)
                    continue

                final_content = response_msg.content
                break
            else:
                break

        # Fallback: scan backwards for the last assistant message with content
        if final_content is None and messages:
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content", "").strip():
                    final_content = msg["content"]
                    break

        # If still nothing, compile tool results as a summary
        if not final_content or not final_content.strip():
            tool_results = []
            for msg in messages:
                if msg.get("role") == "tool" and msg.get("content"):
                    tool_results.append(msg["content"])
            if tool_results:
                final_content = "\n\n".join(tool_results[-3:])  # Last 3 tool results

        # Store conversation history for potential continuation
        self._messages = messages

        return final_content or ""

    _EC.run = _patched_run
    logger.info("[PERSONA-AGENT] Patched ExecutionContext.run() — scoped to delegate agents only")
except Exception as _patch_err:
    logger.warning(f"[PERSONA-AGENT] Failed to patch ExecutionContext.run(): {_patch_err}")
    # Falls back to original behavior + raw fallback in _execute()

AVAILABLE_FUNCTIONS = [
    'delegate_task',
    'check_delegates',
    'get_delegate_result',
    'cancel_delegate',
    'send_message',
    'shared_context_write',
    'shared_context_read',
    'sub_delegate',
    'record_lesson',
    'contradict_lesson',
    'write_agent_skills',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "delegate_task",
            "description": (
                "Delegate a task to a persona-agent specialist and wait for their result.\n"
                "The agent runs with the persona's full personality and a focused toolset.\n\n"
                "The persona-agent will:\n"
                "1. Acknowledge the task in character\n"
                "2. Use their tools to complete it\n"
                "3. Report back with results in character\n\n"
                "This tool blocks until the agent finishes and returns their full report.\n"
                "You do NOT need to call get_delegate_result afterwards — the result is returned directly.\n"
                "After receiving the result, summarize the findings for the user in your own words.\n\n"
                "Example: delegate_task(persona='researcher', task='Research the latest news on AI')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "description": "Name of the persona to delegate to (e.g. 'researcher', 'engineer'). Must be an existing persona."
                    },
                    "task": {
                        "type": "string",
                        "description": "Clear description of what you need them to do. Be specific — this is their only instruction."
                    },
                    "toolset": {
                        "type": "string",
                        "description": "Override toolset (optional). If empty, uses the persona's default toolset from their settings."
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context from the current conversation to pass along (e.g. relevant data, user preferences)."
                    }
                },
                "required": ["persona", "task"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "check_delegates",
            "description": "Check the status of all active persona-agent delegates. Shows who's working, who's done, and what tools they used.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_delegate_result",
            "description": "Get a completed persona-agent's report. Returns their in-character response with results. Auto-dismisses after retrieval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delegate_id": {
                        "type": "string",
                        "description": "The delegate's ID (from delegate_task or check_delegates)"
                    }
                },
                "required": ["delegate_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "cancel_delegate",
            "description": (
                "Cancel a running delegate. By default this is graceful — the delegate finishes their current "
                "tool call and stops. Set force=true to stop them immediately (partial results may be lost).\n"
                "Use check_delegates first to see who's running."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delegate_id": {
                        "type": "string",
                        "description": "The delegate's ID (from check_delegates)"
                    },
                    "force": {
                        "type": "string",
                        "description": "Set to 'true' for immediate cancellation. Default is graceful (finishes current tool)."
                    }
                },
                "required": ["delegate_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "send_message",
            "description": (
                "Send a follow-up message to a completed delegate, continuing their conversation with full context.\n"
                "The delegate resumes with their entire previous conversation history intact — they remember "
                "everything from their first task. Use this for multi-step work: first delegate a task, then "
                "follow up with corrections, additional instructions, or a second phase.\n\n"
                "The delegate must have finished (done/failed/cancelled) before you can send a follow-up.\n"
                "Do NOT use this for new unrelated tasks — use delegate_task instead.\n\n"
                "Example: send_message(delegate_id='abc123', message='Good work. Now also check the SSL certificate expiry.')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delegate_id": {
                        "type": "string",
                        "description": "The delegate's ID (from delegate_task or check_delegates)"
                    },
                    "message": {
                        "type": "string",
                        "description": "The follow-up instruction or question for the delegate"
                    }
                },
                "required": ["delegate_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "shared_context_write",
            "description": (
                "Write a finding or piece of information to the shared team scratchpad.\n"
                "Other agents working on the same session can read this. Use it to share "
                "discoveries, results, or context that other specialists might need.\n\n"
                "Example: shared_context_write(key='server_status', value='nginx is down on port 443, "
                "SSL cert expired 2 days ago')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "A short label for this finding (e.g. 'server_status', 'research_results', 'error_log')"
                    },
                    "value": {
                        "type": "string",
                        "description": "The information to share with the team"
                    }
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "shared_context_read",
            "description": (
                "Read the shared team scratchpad to see what other agents have found.\n"
                "Returns all entries written by any agent in this session.\n"
                "Use this before starting work to check if another specialist already found relevant info."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "sub_delegate",
            "description": (
                "Spawn a helper agent to handle a sub-task. Use this when your main task "
                "requires work outside your specialty — e.g., an engineer needing web research, "
                "or a researcher needing a command run.\n\n"
                "The helper runs with fewer tool rounds (max 5) and returns their result directly.\n"
                "You stay in control — review their output and incorporate it into your work.\n\n"
                "Example: sub_delegate(persona='researcher', task='Find the latest Python 3.13 changelog')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "description": "Name of the persona to sub-delegate to. Must be a specialist, not a coordinator."
                    },
                    "task": {
                        "type": "string",
                        "description": "Clear description of the sub-task. Be specific."
                    }
                },
                "required": ["persona", "task"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "record_lesson",
            "description": (
                "Record something you learned during this task for future reference.\n"
                "Next time you're activated, you'll see your past lessons before starting work.\n\n"
                "Use this for:\n"
                "- Tool quirks you discovered ('pip on this system needs --user flag')\n"
                "- Service issues ('api.example.com requires Bearer auth header')\n"
                "- User preferences ('user prefers verbose output with explanations')\n"
                "- Workarounds that worked ('site X blocks requests without User-Agent header')\n\n"
                "Categories control how long the lesson persists:\n"
                "- 'temporary': 24 hours (service outages, transient errors)\n"
                "- 'session': 7 days (project-specific workarounds, current context)\n"
                "- 'permanent': 90 days (system config, tool behavior, user preferences)\n\n"
                "Keep lessons short and specific — 1-2 sentences max."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lesson": {
                        "type": "string",
                        "description": "What you learned (1-2 sentences). Be specific and actionable."
                    },
                    "category": {
                        "type": "string",
                        "enum": ["temporary", "session", "permanent"],
                        "description": "How long to remember this. 'temporary'=24h, 'session'=7d, 'permanent'=90d"
                    }
                },
                "required": ["lesson", "category"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "contradict_lesson",
            "description": (
                "Mark one of your past lessons as wrong or outdated.\n"
                "Use this when you discover that a previous lesson no longer applies "
                "(site came back up, tool was fixed, approach changed).\n"
                "This weakens the lesson — if contradicted enough times it's automatically removed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lesson": {
                        "type": "string",
                        "description": "The lesson text to contradict (roughly match the original wording)"
                    }
                },
                "required": ["lesson"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "write_agent_skills",
            "description": (
                "Create or update a persona's skills.md file — the role definition, triggers, "
                "and approach patterns that guide how the agent works.\n\n"
                "Modes:\n"
                "- 'auto': Auto-generate from the persona's toolset (includes YAML frontmatter, "
                "approach patterns, tool guidelines, and boundaries). Best for initializing a new persona.\n"
                "- 'manual': Write custom skills content exactly as provided. Use when the user "
                "dictates specific role instructions.\n"
                "- 'augment': Append content to the existing skills file without overwriting. "
                "Use to add new sections or tips to an already-good skills file.\n\n"
                "Example: write_agent_skills(persona_name='scout', mode='auto')\n"
                "Example: write_agent_skills(persona_name='neo', mode='manual', content='# Neo — Engineer\\n...')\n"
                "Example: write_agent_skills(persona_name='scout', mode='augment', content='## Finance Tips\\n...')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "persona_name": {
                        "type": "string",
                        "description": "Name of the persona whose skills to write (e.g. 'scout', 'neo')"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "manual", "augment"],
                        "description": "Generation mode: 'auto' (from toolset), 'manual' (custom content), 'augment' (append to existing)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Skills content to write (required for 'manual' and 'augment' modes, ignored for 'auto')"
                    }
                },
                "required": ["persona_name"]
            }
        }
    },
]


# ── Session & Delegate Storage ───────────────────────────────────────────────
# Shared state — registered in sys.modules so routes (exec()'d separately) can access it

_delegates = {}        # id -> PersonaDelegate
_sessions = {}         # chat_name -> session dict (transcript for visual panel)
_shared_ctx = {}       # chat_name -> {key: {value, author, timestamp}} — shared scratchpad
_lock = threading.Lock()

# Persistence — save session transcripts so they survive restarts
_STATE_FILE = Path(__file__).parent.parent.parent.parent / 'user' / 'plugin_state' / 'persona-agents.json'


def _save_sessions():
    """Persist session transcripts to disk."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            data = {'sessions': _sessions}
        _STATE_FILE.write_text(json.dumps(data, default=str), encoding='utf-8')
    except Exception as e:
        logger.debug(f"[PERSONA-AGENT] Failed to save sessions: {e}")


def _load_sessions():
    """Restore session transcripts from disk on startup."""
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding='utf-8'))
            restored = data.get('sessions', {})
            _sessions.update(restored)
            logger.info(f"[PERSONA-AGENT] Restored {len(restored)} session(s) from disk")
    except Exception as e:
        logger.warning(f"[PERSONA-AGENT] Failed to load sessions: {e}")


# Load saved sessions on module init
_load_sessions()

# Create a lightweight module for shared state access from routes
import types as _types
_shared = _types.ModuleType("persona_agents_shared")
_shared._delegates = _delegates
_shared._sessions = _sessions
_shared._lock = _lock
_sys.modules["persona_agents_shared"] = _shared


def _strip_thinking(text):
    """Remove thinking tags from LLM output."""
    if not text:
        return ''
    result = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
    result = re.sub(r'<\|channel>thought[\s\S]*?(?=<\|channel>|$)', '', result)
    result = re.sub(r'</?think>\s*', '', result)
    return result.strip()


def _extract_thinking_content(text):
    """Last-resort fallback: extract readable content from thinking tags.

    When the LLM only produced <think> blocks and even the summary call failed,
    we strip the tags and present the thinking as a best-effort report.
    Better than returning nothing.
    """
    if not text:
        return ''
    # Pull content from inside think tags
    chunks = re.findall(r'<think>([\s\S]*?)</think>', text)
    if chunks:
        content = '\n'.join(c.strip() for c in chunks if c.strip())
    else:
        # Maybe unclosed think tag — just strip the tag markers
        content = re.sub(r'</?think>', '', text).strip()
        content = re.sub(r'<\|channel>thought', '', content).strip()

    if content:
        return f"[Note: Agent ran out of tool rounds. Partial findings from their working notes:]\n\n{content}"
    return ''


class PersonaDelegate:
    """A persona-powered background agent."""

    def __init__(self, delegate_id, persona_name, persona_data, task, toolset,
                 context, chat_name, on_complete=None):
        self.id = delegate_id
        self.persona_name = persona_name
        self.persona_data = persona_data
        self.task = task
        self.toolset = toolset
        self.context = context
        self.chat_name = chat_name
        self.status = 'running'  # running | done | failed | cancelled
        self.result = None
        self.error = None
        self.tool_log = []
        self.start_time = time.time()
        self.end_time = None
        self._thread = None
        self._on_complete = on_complete
        self._cancel = threading.Event()       # Graceful: finish current tool, then stop
        self._force_cancel = threading.Event()  # Immediate: stop ASAP
        self._messages = None  # Conversation history for continuation
        self._ctx = None       # ExecutionContext for continuation

        # Persona visual info
        settings = persona_data.get('settings', {})
        self.display_name = persona_data.get('name', persona_name)
        self.trim_color = settings.get('trim_color', '#4a9eff')
        self.voice = settings.get('voice', '')
        self.pitch = settings.get('pitch', 1.0)
        self.speed = settings.get('speed', 1.0)
        self.avatar = persona_data.get('avatar')
        self.tagline = persona_data.get('tagline', '')

    @property
    def elapsed(self):
        end = self.end_time or time.time()
        return round(end - self.start_time, 1)

    @property
    def is_cancelled(self):
        return self._cancel.is_set() or self._force_cancel.is_set()

    def cancel(self, force=False):
        """Request cancellation. force=True stops immediately, False finishes current tool."""
        if force:
            self._force_cancel.set()
        self._cancel.set()

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f'persona-agent-{self.persona_name}-{self.id}'
        )
        self._thread.start()

    def _run(self):
        # Set delegate identity for MemPalace (thread-local) so memory tools
        # know which persona is calling from this delegate thread.
        _mp_identity_set = False
        try:
            from plugins.mempalace.tools.mempalace_tools import set_delegate_persona, clear_delegate_persona
            set_delegate_persona(self.persona_name)
            _mp_identity_set = True
        except ImportError:
            pass  # MemPalace not installed — no-op
        except Exception:
            pass

        try:
            self._execute()
            if self.is_cancelled:
                self.status = 'cancelled'
            else:
                self.status = 'done'
        except _DelegateCancelled:
            self.status = 'cancelled'
            self.result = self.result or '(Cancelled by lead)'
        except Exception as e:
            logger.error(f"[PERSONA-AGENT] {self.persona_name} failed: {e}", exc_info=True)
            self.status = 'failed'
            self.error = str(e)
        finally:
            # Clear delegate identity
            if _mp_identity_set:
                try:
                    clear_delegate_persona()
                except Exception:
                    pass
            self.end_time = time.time()
            # Log result to dedicated log
            log_result(
                delegate_id=self.id,
                persona=self.persona_name,
                display_name=self.display_name,
                status=self.status,
                elapsed=self.elapsed,
                tool_log=self.tool_log,
                result_preview=self.result or '',
                error=self.error or '',
            )
            # Add result to visual session
            _add_to_session(self)
            # Publish completion event for reactive UI
            try:
                from core.event_bus import publish
                publish('delegate_completed', {
                    'id': self.id,
                    'persona': self.persona_name,
                    'display_name': self.display_name,
                    'status': self.status,
                    'elapsed': self.elapsed,
                    'tool_log': self.tool_log,
                    'trim_color': self.trim_color,
                    'chat_name': self.chat_name,
                })
            except Exception:
                pass
            # Notify batch complete
            if self._on_complete:
                try:
                    self._on_complete(self.id, self.chat_name)
                except Exception:
                    pass

    def _execute(self):
        """Run the persona's task using ExecutionContext."""
        from core.continuity.execution_context import ExecutionContext
        from core.api_fastapi import get_system

        system = get_system()
        fm = system.llm_chat.function_manager
        te = system.llm_chat.tool_engine

        # Load the persona's prompt
        settings = self.persona_data.get('settings', {})
        prompt_name = settings.get('prompt', 'sapphire')

        # Determine provider from persona settings
        provider_key = settings.get('llm_primary', 'auto')
        model_override = settings.get('llm_model', '')

        # Build task settings — delegates use minimal scopes to stay within
        # context limits. They're doing a focused task, not a full conversation.
        # Detect context limit from provider config for budget tracking
        context_budget = 0
        try:
            import config as _cfg
            context_budget = getattr(_cfg, 'CONTEXT_LIMIT', 0)
            # Delegates get 70% of the total context to leave room for the lead
            if context_budget:
                context_budget = int(context_budget * 0.7)
        except Exception:
            pass

        # Sub-delegates get fewer rounds to keep them focused
        sub_depth = getattr(self, '_sub_depth', 0)
        max_rounds = 5 if sub_depth > 0 else 10

        # Determine memory scope for delegates based on IFTTT bridge mode.
        # When MemPalace is active: keep 'none' — memories injected directly via bridge.
        # When 'standard' mode: unlock to 'default' so old tools actually work.
        # When 'none' mode: keep 'none' — no memory at all.
        _delegate_mem_scope = 'none'
        _delegate_know_scope = 'none'
        if _has_mempalace_bridge:
            _mode = _mp_bridge.get_memory_mode()
            if _mode == 'standard':
                _delegate_mem_scope = 'default'
                _delegate_know_scope = 'default'
            # 'auto' with no mempalace → also unlock old tools
            elif _mode == 'auto' and not _mp_bridge.should_use_mempalace():
                _delegate_mem_scope = 'default'
                _delegate_know_scope = 'default'

        task_settings = {
            'prompt': prompt_name,
            'toolset': self.toolset,
            'provider': provider_key if provider_key != 'auto' else 'auto',
            'model': model_override,
            'max_tool_rounds': max_rounds,
            'max_parallel_tools': 3,
            'context_limit': context_budget,
            'inject_datetime': True,
            'memory_scope': _delegate_mem_scope,
            'knowledge_scope': _delegate_know_scope,
            'goal_scope': 'none',
            'email_scope': 'none',
            'bitcoin_scope': 'none',
            'gcal_scope': 'none',
            'telegram_scope': 'none',
            'discord_scope': 'none',
        }

        ctx = ExecutionContext(fm, te, task_settings)
        ctx._persona_agent = True       # Flag for patched run() to preserve content
        ctx._cancel_event = self._cancel        # Graceful cancel
        ctx._force_cancel_event = self._force_cancel  # Force cancel

        # ── IFTTT MemPalace integration ────────────────────────────────────
        # If MemPalace is installed and enabled, inject memory layers into the
        # delegate's system prompt and swap old memory tools for MemPalace tools.
        # This happens AFTER ExecutionContext construction so we can modify
        # ctx.system_prompt and ctx.tools directly.
        if _has_mempalace_bridge and _mp_bridge.should_use_mempalace():
            # Inject L0/L1/L2 memory layers into system prompt
            mem_block = _mp_bridge.get_memory_injection(self.persona_name, self.task)
            if mem_block:
                ctx.system_prompt += mem_block

            # Swap old memory/knowledge tools for MemPalace equivalents
            _mp_bridge.swap_tools_in_ctx(ctx)

        # ── Build the delegation prompt ────────────────────────────────────
        # Tells the persona who they are, what tools they have, past lessons,
        # team context, and the actual task.

        # Resolve what tools this delegate actually has (for self-awareness)
        # Read from ctx.tools (post-swap) so the delegate sees its actual tools
        tool_list_str = ""
        try:
            if self.toolset == "all":
                tool_list_str = "(full toolset — all tools available)"
            elif ctx.tools:
                fn_names = [t['function']['name'] for t in ctx.tools if 'function' in t]
                if fn_names:
                    tool_list_str = ", ".join(fn_names[:20])
                    if len(fn_names) > 20:
                        tool_list_str += f" ... and {len(fn_names) - 20} more"
        except Exception:
            pass

        # Build workflow steps — adapts based on whether MemPalace is active
        _mp_active = _has_mempalace_bridge and _mp_bridge.should_use_mempalace()

        if _mp_active:
            recall_step = (
                f"2. RECALL MEMORIES: Check your [MemPalace — Your Memories] section in the system prompt. "
                f"If you need deeper context, use memory_search or memory_recall.\n"
            )
            remember_step = (
                f"   - Use memory_remember to save important findings or results for future tasks.\n"
            )
        else:
            recall_step = ""
            remember_step = ""

        # Step numbering shifts when MemPalace adds a step
        if _mp_active:
            steps = (
                f"YOUR WORKFLOW — follow these steps IN ORDER:\n"
                f"1. ACKNOWLEDGE: Brief in-character greeting (1-2 sentences showing personality)\n"
                f"{recall_step}"
                f"3. CHECK LESSONS: Read your [Past Experience] section below (if any). Adapt your approach based on what you've learned before.\n"
                f"4. DO THE WORK: Use your tools to complete the task.\n"
                f"5. SHARE: If you found something other team members need, call shared_context_write.\n"
                f"6. REFLECT: Before signing off, call record_lesson for EACH of these you encountered:\n"
                f"   - Something that FAILED or was unexpected (category='temporary' if transient, 'session' if ongoing)\n"
                f"   - A trick/workaround that WORKED (category='session' or 'permanent')\n"
                f"   - A system/tool quirk worth remembering (category='permanent')\n"
                f"{remember_step}"
                f"   If the task was straightforward and nothing surprising happened, skip this step.\n"
                f"7. REPORT: Give your results with a brief in-character sign-off.\n\n"
            )
        else:
            steps = (
                f"YOUR WORKFLOW — follow these steps IN ORDER:\n"
                f"1. ACKNOWLEDGE: Brief in-character greeting (1-2 sentences showing personality)\n"
                f"2. CHECK LESSONS: Read your [Past Experience] section below (if any). Adapt your approach based on what you've learned before.\n"
                f"3. DO THE WORK: Use your tools to complete the task.\n"
                f"4. SHARE: If you found something other team members need, call shared_context_write.\n"
                f"5. REFLECT: Before signing off, call record_lesson for EACH of these you encountered:\n"
                f"   - Something that FAILED or was unexpected (category='temporary' if transient, 'session' if ongoing)\n"
                f"   - A trick/workaround that WORKED (category='session' or 'permanent')\n"
                f"   - A system/tool quirk worth remembering (category='permanent')\n"
                f"   If the task was straightforward and nothing surprising happened, skip this step.\n"
                f"6. REPORT: Give your results with a brief in-character sign-off.\n\n"
            )

        delegation_prompt = (
            f"[Team Delegation]\n"
            f"You've been called in to help with a task. You are {self.display_name}.\n"
            f"Stay fully in character throughout.\n\n"
            f"{steps}"
            f"⚠️ CRITICAL — NEVER FABRICATE:\n"
            f"- If you CANNOT access content (video, paywalled site, broken link), say so CLEARLY.\n"
            f"- NEVER invent timestamps, quotes, summaries, or data you didn't actually retrieve.\n"
            f"- 'I couldn't access this' is ALWAYS better than a made-up answer.\n"
            f"- If a tool returns an error or empty result, report what happened honestly.\n"
            f"- Partial info is fine — just label what's confirmed vs what you couldn't verify.\n\n"
            f"⚠️ CRITICAL — ALWAYS PRODUCE VISIBLE OUTPUT:\n"
            f"- Your response MUST contain visible text outside of <think> tags.\n"
            f"- After thinking, you MUST write your report/answer as plain visible text.\n"
            f"- If your entire response is inside <think> tags, your lead will see NOTHING.\n"
            f"- Even if results are incomplete, write what you found as visible text.\n"
        )

        # Self-awareness: tell the delegate what tools they have
        if tool_list_str:
            delegation_prompt += (
                f"\n[Your Tools]\n"
                f"Toolset: {self.toolset}\n"
                f"Available: {tool_list_str}\n"
                f"Use ONLY these tools. Do not attempt to call tools you don't have.\n"
            )

        # Inject skills definition (role, guidelines, boundaries)
        skills_block = _skills_get_prompt(self.persona_name)
        if skills_block:
            delegation_prompt += f"\n\n{skills_block}\n"

        delegation_prompt += f"\nTASK: {self.task}"

        if self.context:
            delegation_prompt += f"\n\nCONTEXT: {self.context}"

        # Inject past lessons for this persona (persistent learning)
        lessons_block = _lessons_get_prompt(self.persona_name)
        if lessons_block:
            delegation_prompt += f"\n\n{lessons_block}"

        # Inject shared context from other delegates
        with _lock:
            team_ctx = _shared_ctx.get(self.chat_name, {})
        if team_ctx:
            ctx_lines = ["[Team Findings — shared by other agents]"]
            for key, entry in team_ctx.items():
                ctx_lines.append(f"  {key} (from {entry['author']}): {entry['value']}")
            delegation_prompt += "\n\n" + "\n".join(ctx_lines)

        log_event("EXEC", f"{self.persona_name} starting execution (toolset={self.toolset}, provider={provider_key})")

        raw = ctx.run(delegation_prompt)

        # Save context for potential continuation via send_message
        self._ctx = ctx
        self._messages = getattr(ctx, '_messages', None)

        logger.info(f"[PERSONA-AGENT] {self.persona_name} raw result type={type(raw).__name__}, "
                     f"len={len(raw) if raw else 0}, preview={repr((raw or '')[:200])}")

        self.tool_log = ctx.tool_log

        # Try to strip thinking tags, but if that empties the result,
        # the delegate probably ran out of tool rounds while still thinking.
        # Force one more LLM call to produce a visible summary.
        result = _strip_thinking(raw) if raw else ''
        if not result and raw and raw.strip():
            logger.info(f"[PERSONA-AGENT] {self.persona_name} result is thinking-only, "
                        f"requesting summary call (raw_len={len(raw)})")
            result = self._force_summary(ctx, raw)

        if not result:
            result = None  # None = no result, '' would be falsy ambiguity

        self.result = result

        # Log each tool that was called
        for tool_name in self.tool_log:
            log_tool_call(self.id, self.persona_name, tool_name)

    def _force_summary(self, ctx, raw_thinking):
        """When the delegate ran out of tool rounds and only produced thinking,
        make one more LLM call (no tools) asking it to report what it found.

        This turns the thinking content into a visible report instead of
        returning empty or raw <think> tags to the lead.
        """
        try:
            # Build a minimal message list: system prompt + summary request
            messages = getattr(ctx, '_messages', []) or []

            # Add a final user message forcing a visible report
            summary_prompt = (
                "[SYSTEM — Tool limit reached]\n"
                "You've used all your tool rounds. You MUST now produce your final report.\n"
                "Summarize EVERYTHING you found so far as visible text (NOT inside <think> tags).\n"
                "Include all data, findings, and sources you gathered. Even partial results are valuable.\n"
                "Write your report NOW — this is your last chance to communicate your findings."
            )

            summary_messages = list(messages) + [
                {"role": "user", "content": summary_prompt}
            ]

            # Use the delegate's own provider (from ExecutionContext) — no separate import
            provider = getattr(ctx, 'provider', None)
            if not provider:
                logger.warning(f"[PERSONA-AGENT] {self.persona_name} no provider for summary call")
                return _extract_thinking_content(raw_thinking)

            response = provider.chat_completion(
                messages=summary_messages,
                tools=None,  # No tools — force text response
                generation_params={'max_tokens': 2000, 'temperature': 0.7},
            )

            if response and response.content:
                visible = _strip_thinking(response.content)
                if visible:
                    logger.info(f"[PERSONA-AGENT] {self.persona_name} summary call produced "
                               f"{len(visible)} chars of visible content")
                    return visible

            # If summary call also failed, extract what we can from thinking
            logger.warning(f"[PERSONA-AGENT] {self.persona_name} summary call produced no visible content")
            return _extract_thinking_content(raw_thinking)

        except Exception as e:
            logger.warning(f"[PERSONA-AGENT] {self.persona_name} summary call failed: {e}")
            return _extract_thinking_content(raw_thinking)

    def to_dict(self):
        return {
            'id': self.id,
            'persona': self.persona_name,
            'display_name': self.display_name,
            'task': self.task,
            'status': self.status,
            'elapsed': self.elapsed,
            'tool_log': self.tool_log,
            'trim_color': self.trim_color,
            'avatar': self.avatar,
            'tagline': self.tagline,
            'voice': self.voice,
            'pitch': self.pitch,
            'speed': self.speed,
            'has_result': self.result is not None,
            'error': self.error,
        }


# ── Visual Session (Round Table style transcript) ────────────────────────────

def _get_session(chat_name):
    """Get or create a visual session for this chat."""
    if chat_name not in _sessions:
        _sessions[chat_name] = {
            'id': uuid.uuid4().hex[:8],
            'chat_name': chat_name,
            'transcript': [],
            'created_at': datetime.now().isoformat(),
        }
    return _sessions[chat_name]


def _add_to_session(delegate):
    """Add a delegate's result to the visual transcript."""
    session = _get_session(delegate.chat_name)

    # Dispatch entry
    entry = {
        'type': 'dispatch',
        'persona': delegate.persona_name,
        'display_name': delegate.display_name,
        'task': delegate.task,
        'trim_color': delegate.trim_color,
        'avatar': delegate.avatar,
        'timestamp': datetime.fromtimestamp(delegate.start_time).isoformat(),
    }
    # Only add dispatch if not already there
    if not any(e.get('type') == 'dispatch' and e.get('persona') == delegate.persona_name
               and e.get('task') == delegate.task for e in session['transcript']):
        session['transcript'].append(entry)

    # Result entry
    result_entry = {
        'type': 'result',
        'persona': delegate.persona_name,
        'display_name': delegate.display_name,
        'status': delegate.status,
        'content': delegate.result if delegate.result is not None and delegate.result != '' else (delegate.error or '(No result)'),
        'tool_log': delegate.tool_log,
        'elapsed': delegate.elapsed,
        'trim_color': delegate.trim_color,
        'avatar': delegate.avatar,
        'voice': delegate.voice,
        'pitch': delegate.pitch,
        'speed': delegate.speed,
        'timestamp': datetime.now().isoformat(),
    }
    session['transcript'].append(result_entry)

    # Persist to disk so transcripts survive restarts
    _save_sessions()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_active_chat():
    """Get the current active chat name."""
    try:
        from core.api_fastapi import get_system
        return get_system().llm_chat.get_active_chat() or 'default'
    except Exception:
        return 'default'


def _load_persona(persona_name):
    """Load a persona's full data."""
    try:
        from core.personas.persona_manager import persona_manager
        return persona_manager.get(persona_name)
    except Exception as e:
        logger.error(f"[PERSONA-AGENT] Failed to load persona '{persona_name}': {e}")
        return None


def _list_all_personas():
    """List all available persona names."""
    try:
        from core.personas.persona_manager import persona_manager
        return list(persona_manager.get_all().keys())
    except Exception:
        return []


def _check_batch_complete(delegate_id, chat_name):
    """Called when a delegate finishes. Logs completion.

    In Manual mode: notification to the lead persona happens via prompt_inject
    hook on the next user message.
    In Auto mode: backend sends a continuation message to trigger the lead
    persona to pick up results and keep going.
    """
    with _lock:
        chat_delegates = [d for d in _delegates.values() if d.chat_name == chat_name]
        if not chat_delegates:
            return
        still_running = any(d.status == 'running' for d in chat_delegates)

    log_batch_complete(chat_name, len(chat_delegates))

    # Auto-continue: if enabled and no delegates still running, nudge from backend
    # This covers both browser and headless (scheduled tasks) scenarios
    if not still_running and getattr(_shared, '_auto_continue', False):
        # Debounce — wait briefly in case multiple delegates finish near-simultaneously
        threading.Timer(2.0, _backend_nudge, args=[chat_name]).start()


_nudge_pending = {}  # chat_name -> True if a nudge is already scheduled

def _backend_nudge(chat_name):
    """Send a continuation message to trigger the lead persona to pick up results."""
    # Prevent double nudges
    if _nudge_pending.get(chat_name):
        return
    _nudge_pending[chat_name] = True

    try:
        from core.api_fastapi import get_system
        import asyncio

        system = get_system()
        msg = "[Delegate reports are ready — review results and continue your task]"

        # Use the streaming chat to send and get a response
        loop = asyncio.get_event_loop()
        if loop.is_running():
            async def _do_nudge():
                try:
                    await system.llm_chat.streaming_chat.send_and_stream(
                        msg, chat_name=chat_name,
                    )
                finally:
                    _nudge_pending.pop(chat_name, None)
            loop.create_task(_do_nudge())
        else:
            _nudge_pending.pop(chat_name, None)

        log_event("AUTO-CONTINUE", f"Backend nudge sent for chat={chat_name}")
    except Exception as e:
        _nudge_pending.pop(chat_name, None)
        log_event("AUTO-CONTINUE-ERR", f"Backend nudge failed: {e}")


# ── Tool Execution ───────────────────────────────────────────────────────────

def execute(function_name, arguments, config, plugin_settings=None):

    if function_name == 'delegate_task':
        return _delegate_task(arguments)

    elif function_name == 'check_delegates':
        return _check_delegates()

    elif function_name == 'get_delegate_result':
        return _get_delegate_result(arguments)

    elif function_name == 'cancel_delegate':
        return _cancel_delegate(arguments)

    elif function_name == 'send_message':
        return _send_message(arguments)

    elif function_name == 'shared_context_write':
        return _shared_context_write(arguments)

    elif function_name == 'shared_context_read':
        return _shared_context_read(arguments)

    elif function_name == 'sub_delegate':
        return _sub_delegate(arguments)

    elif function_name == 'record_lesson':
        return _record_lesson(arguments)

    elif function_name == 'contradict_lesson':
        return _contradict_lesson(arguments)

    elif function_name == 'write_agent_skills':
        return _write_agent_skills(arguments)

    return f"Unknown function: {function_name}", False


def _delegate_task(arguments):
    """Spawn a persona-powered agent to handle a task."""
    persona_name = arguments.get('persona', '').strip().lower()
    task = arguments.get('task', '').strip()
    toolset_override = arguments.get('toolset', '').strip()
    context = arguments.get('context', '').strip()

    if not persona_name:
        available = ', '.join(_list_all_personas())
        return f"ERROR: persona name is required. Available personas: {available}", False

    if not task:
        return "ERROR: task description is required.", False

    # Load persona
    persona_data = _load_persona(persona_name)
    if not persona_data:
        available = ', '.join(_list_all_personas())
        return (
            f"ERROR: Persona '{persona_name}' not found. "
            f"Available personas: {available}",
            False
        )

    # Determine toolset: validated override > persona setting > fallback to 'conversation'
    settings = persona_data.get('settings', {})
    persona_toolset = settings.get('toolset', '') or 'conversation'
    # Only use override if it's an actual valid toolset name
    if toolset_override:
        from core.toolsets import toolset_manager
        if toolset_manager.toolset_exists(toolset_override):
            toolset = toolset_override
        else:
            toolset = persona_toolset  # Invalid override — use persona's default
    else:
        toolset = persona_toolset

    # Check concurrent limit + prune stale completed delegates (>10 min old)
    with _lock:
        stale_cutoff = time.time() - 600
        stale_ids = [
            d.id for d in _delegates.values()
            if d.status in ('done', 'failed', 'cancelled')
            and d.end_time and d.end_time < stale_cutoff
        ]
        for sid in stale_ids:
            _delegates.pop(sid, None)

        active_count = sum(1 for d in _delegates.values() if d.status == 'running')
        if active_count >= 3:
            return "ERROR: Maximum 3 delegates can run at once. Wait for one to finish or check results.", False

        delegate_id = uuid.uuid4().hex[:8]
        chat_name = _get_active_chat()

        delegate = PersonaDelegate(
            delegate_id=delegate_id,
            persona_name=persona_name,
            persona_data=persona_data,
            task=task,
            toolset=toolset,
            context=context,
            chat_name=chat_name,
            on_complete=_check_batch_complete,
        )
        _delegates[delegate_id] = delegate

    # Add dispatch notice to visual session
    session = _get_session(chat_name)
    session['transcript'].append({
        'type': 'dispatch',
        'persona': persona_name,
        'display_name': delegate.display_name,
        'task': task,
        'toolset': toolset,
        'trim_color': delegate.trim_color,
        'avatar': delegate.avatar,
        'timestamp': datetime.now().isoformat(),
    })
    _save_sessions()

    delegate.start()

    logger.info(f"[PERSONA-AGENT] Delegated to {persona_name} (id={delegate_id}, toolset={toolset}): {task[:80]}")
    log_dispatch(
        delegate_id=delegate_id,
        persona=persona_name,
        display_name=delegate.display_name,
        task=task,
        toolset=toolset,
        chat_name=chat_name,
    )

    # Publish dispatch event for reactive UI
    try:
        from core.event_bus import publish
        publish('delegate_dispatched', {
            'id': delegate_id,
            'persona': persona_name,
            'display_name': delegate.display_name,
            'task': task,
            'toolset': toolset,
            'trim_color': delegate.trim_color,
            'chat_name': chat_name,
        })
    except Exception:
        pass

    # ── Synchronous wait — block until delegate finishes ──────────────────
    # This keeps the lead persona's tool-call loop alive so they get the result directly
    # without needing a nudge or get_delegate_result call.
    # SSE events still fire during the wait so the UI stays responsive.
    MAX_WAIT = 300  # 5 minute safety cap
    poll_interval = 0.5
    waited = 0.0
    while delegate.status == 'running' and waited < MAX_WAIT:
        time.sleep(poll_interval)
        waited += poll_interval

    if delegate.status == 'running':
        # Timed out — fall back to async mode
        return (
            f"{delegate.display_name} is still working (>{MAX_WAIT}s elapsed).\n"
            f"Use check_delegates to monitor, then get_delegate_result when done.",
            True
        )

    # Delegate finished — return the full result directly
    # Delegate stays in _delegates for potential send_message continuation.
    # Cleaned up by get_delegate_result or when a new delegate takes the slot.
    tools_used = ', '.join(delegate.tool_log) if delegate.tool_log else 'none'
    result = delegate.result or delegate.error or '(No result)'

    logger.info(f"[PERSONA-AGENT] Synchronous return for {delegate.display_name}: "
                f"status={delegate.status}, result_len={len(result)}, "
                f"result_preview={repr(result[:150])}")

    status_icon = '\u2705' if delegate.status == 'done' else '\u274c'
    return (
        f"{status_icon} {delegate.display_name} — {delegate.status} in {delegate.elapsed}s | tools: {tools_used}\n\n"
        f"{result}",
        True
    )


def _check_delegates():
    """Check status of all persona delegates."""
    chat_name = _get_active_chat()
    with _lock:
        delegates = [d for d in _delegates.values() if d.chat_name == chat_name]

    if not delegates:
        return "No active persona delegates.", True

    lines = [f"Persona Delegates ({len(delegates)}):"]
    for d in delegates:
        icon = {
            'running': '\U0001f7e1', 'done': '\U0001f7e2', 'failed': '\U0001f534',
            'cancelled': '\U0001f7e0',
        }.get(d.status, '\u2753')
        lines.append(f"  {icon} {d.display_name} [{d.id}] \u2014 {d.status} ({d.elapsed}s)")
        lines.append(f"      Task: {d.task[:100]}")
        lines.append(f"      Toolset: {d.toolset}")
        if d.tool_log:
            lines.append(f"      Tools used: {', '.join(d.tool_log)}")

    return '\n'.join(lines), True


def _get_delegate_result(arguments):
    """Get a completed delegate's report and dismiss them."""
    delegate_id = arguments.get('delegate_id', '').strip()
    if not delegate_id:
        return "ERROR: delegate_id is required.", False

    with _lock:
        delegate = _delegates.get(delegate_id)

    if not delegate:
        return f"ERROR: Delegate '{delegate_id}' not found.", False

    if delegate.status == 'running':
        return (
            f"{delegate.display_name} is still working on it ({delegate.elapsed}s elapsed).\n"
            f"Tools used so far: {', '.join(delegate.tool_log) if delegate.tool_log else 'none yet'}",
            True
        )

    # Get result and clean up
    tools_used = ', '.join(delegate.tool_log) if delegate.tool_log else 'none'
    result = delegate.result or delegate.error or 'No result.'

    with _lock:
        _delegates.pop(delegate_id, None)

    return (
        f"[{delegate.display_name} \u2014 {delegate.status} in {delegate.elapsed}s | tools: {tools_used}]\n\n"
        f"{result}",
        True
    )


def _cancel_delegate(arguments):
    """Cancel a running delegate. Graceful by default, force with force=true."""
    delegate_id = arguments.get('delegate_id', '').strip()
    force = str(arguments.get('force', '')).lower() in ('true', '1', 'yes')

    if not delegate_id:
        return "ERROR: delegate_id is required.", False

    with _lock:
        delegate = _delegates.get(delegate_id)

    if not delegate:
        return f"ERROR: Delegate '{delegate_id}' not found.", False

    if delegate.status != 'running':
        return f"{delegate.display_name} is already {delegate.status}.", True

    mode = 'force' if force else 'graceful'
    delegate.cancel(force=force)
    log_event("CANCEL", f"{delegate.display_name} ({delegate_id}) — {mode} cancel requested")

    if force:
        return (
            f"Force cancel sent to {delegate.display_name}. "
            f"They will stop immediately (partial results may be available via get_delegate_result).",
            True
        )
    return (
        f"Graceful cancel sent to {delegate.display_name}. "
        f"They will finish their current tool call and then stop.",
        True
    )


def _send_message(arguments):
    """Send a follow-up message to a completed delegate, continuing their conversation."""
    delegate_id = arguments.get('delegate_id', '').strip()
    message = arguments.get('message', '').strip()

    if not delegate_id:
        return "ERROR: delegate_id is required.", False
    if not message:
        return "ERROR: message is required.", False

    with _lock:
        delegate = _delegates.get(delegate_id)

    if not delegate:
        return f"ERROR: Delegate '{delegate_id}' not found. They may have been dismissed already.", False

    if delegate.status == 'running':
        return f"{delegate.display_name} is still working. Wait for them to finish first.", True

    if not delegate._messages or not delegate._ctx:
        return (
            f"{delegate.display_name} has no conversation history to continue. "
            f"Use delegate_task to start a new delegation instead.",
            False
        )

    # Reset delegate state for continuation
    delegate.status = 'running'
    delegate.result = None
    delegate.error = None
    delegate.start_time = time.time()
    delegate.end_time = None
    delegate._cancel = threading.Event()
    delegate._force_cancel = threading.Event()

    # Store the follow-up message for the continuation thread
    delegate._continuation_message = message

    def _continue():
        try:
            ctx = delegate._ctx
            ctx._cancel_event = delegate._cancel
            ctx._force_cancel_event = delegate._force_cancel

            # Build the history from previous messages (skip system prompt)
            history = [m for m in delegate._messages if m.get('role') != 'system']

            follow_up = (
                f"[Follow-up from your lead]\n"
                f"{message}"
            )

            log_event("CONTINUE", f"{delegate.persona_name} ({delegate_id}): {message[:80]}")

            raw = ctx.run(follow_up, history_messages=history)

            # Save updated conversation for further continuation
            delegate._messages = getattr(ctx, '_messages', None)
            delegate.tool_log = ctx.tool_log

            result = _strip_thinking(raw) if raw else ''
            if not result and raw and raw.strip():
                result = raw.strip()
            delegate.result = result or None
            delegate.status = 'done' if not delegate.is_cancelled else 'cancelled'

        except _DelegateCancelled:
            delegate.status = 'cancelled'
            delegate.result = delegate.result or '(Cancelled by lead)'
        except Exception as e:
            logger.error(f"[PERSONA-AGENT] {delegate.persona_name} continuation failed: {e}", exc_info=True)
            delegate.status = 'failed'
            delegate.error = str(e)
        finally:
            delegate.end_time = time.time()
            _add_to_session(delegate)
            try:
                from core.event_bus import publish
                publish('delegate_completed', {
                    'id': delegate.id,
                    'persona': delegate.persona_name,
                    'display_name': delegate.display_name,
                    'status': delegate.status,
                    'elapsed': delegate.elapsed,
                    'tool_log': delegate.tool_log,
                    'trim_color': delegate.trim_color,
                    'chat_name': delegate.chat_name,
                })
            except Exception:
                pass

    thread = threading.Thread(
        target=_continue, daemon=True,
        name=f'persona-continue-{delegate.persona_name}-{delegate.id}'
    )
    delegate._thread = thread
    thread.start()

    # Add dispatch notice to session
    session = _get_session(delegate.chat_name)
    session['transcript'].append({
        'type': 'dispatch',
        'persona': delegate.persona_name,
        'display_name': delegate.display_name,
        'task': f'Follow-up: {message[:100]}',
        'toolset': delegate.toolset,
        'trim_color': delegate.trim_color,
        'avatar': delegate.avatar,
        'timestamp': datetime.now().isoformat(),
    })
    _save_sessions()

    try:
        from core.event_bus import publish
        publish('delegate_dispatched', {
            'id': delegate.id,
            'persona': delegate.persona_name,
            'display_name': delegate.display_name,
            'task': f'Follow-up: {message[:100]}',
            'toolset': delegate.toolset,
            'trim_color': delegate.trim_color,
            'chat_name': delegate.chat_name,
        })
    except Exception:
        pass

    # Synchronous wait like delegate_task
    MAX_WAIT = 300
    poll_interval = 0.5
    waited = 0.0
    while delegate.status == 'running' and waited < MAX_WAIT:
        time.sleep(poll_interval)
        waited += poll_interval

    if delegate.status == 'running':
        return (
            f"{delegate.display_name} is still working on the follow-up (>{MAX_WAIT}s).\n"
            f"Use check_delegates to monitor.",
            True
        )

    tools_used = ', '.join(delegate.tool_log) if delegate.tool_log else 'none'
    result = delegate.result or delegate.error or '(No result)'

    with _lock:
        _delegates.pop(delegate_id, None)

    status_icon = '\u2705' if delegate.status == 'done' else '\u274c'
    return (
        f"{status_icon} {delegate.display_name} (follow-up) \u2014 {delegate.status} in {delegate.elapsed}s | tools: {tools_used}\n\n"
        f"{result}",
        True
    )


# ── Shared Context (Team Scratchpad) ─────────────────────────────────────────

def _shared_context_write(arguments):
    """Write to the shared team scratchpad."""
    key = arguments.get('key', '').strip()
    value = arguments.get('value', '').strip()

    if not key:
        return "ERROR: key is required.", False
    if not value:
        return "ERROR: value is required.", False

    chat_name = _get_active_chat()

    with _lock:
        if chat_name not in _shared_ctx:
            _shared_ctx[chat_name] = {}

        # Detect author from current delegate context
        author = 'unknown'
        for d in _delegates.values():
            if d.status == 'running' and d.chat_name == chat_name:
                author = d.display_name
                break

        _shared_ctx[chat_name][key] = {
            'value': value,
            'author': author,
            'timestamp': datetime.now().isoformat(),
        }

    log_event("SHARED-CTX", f"{author} wrote '{key}' ({len(value)} chars)")
    return f"Shared context updated: '{key}' is now available to all team members.", True


def _shared_context_read(arguments=None):
    """Read the shared team scratchpad."""
    chat_name = _get_active_chat()

    with _lock:
        ctx = _shared_ctx.get(chat_name, {})

    if not ctx:
        return "Shared scratchpad is empty. No team members have shared any findings yet.", True

    lines = ["[Team Scratchpad]"]
    for key, entry in ctx.items():
        author = entry.get('author', '?')
        ts = entry.get('timestamp', '')
        ts_short = ts[11:19] if len(ts) > 19 else ts  # HH:MM:SS
        value = entry.get('value', '')
        lines.append(f"  \u2022 {key} (by {author} at {ts_short}):")
        lines.append(f"    {value}")
        lines.append("")

    return '\n'.join(lines), True


# ── Sub-delegation (Hierarchy) ──────────────────────────────────────────────

# Track sub-delegation depth to prevent infinite recursion
_SUB_DELEGATE_DEPTH = threading.local()
MAX_SUB_DEPTH = 2  # A sub-delegate can spawn one more level, but no deeper


def _sub_delegate(arguments):
    """Spawn a helper agent for a sub-task. Lighter-weight than delegate_task."""
    persona_name = arguments.get('persona', '').strip().lower()
    task = arguments.get('task', '').strip()

    if not persona_name:
        available = ', '.join(_list_all_personas())
        return f"ERROR: persona name is required. Available personas: {available}", False
    if not task:
        return "ERROR: task description is required.", False

    # Check recursion depth
    current_depth = getattr(_SUB_DELEGATE_DEPTH, 'depth', 0)
    if current_depth >= MAX_SUB_DEPTH:
        return "ERROR: Maximum sub-delegation depth reached. Complete this task yourself.", False

    # Load persona
    persona_data = _load_persona(persona_name)
    if not persona_data:
        available = ', '.join(_list_all_personas())
        return f"ERROR: Persona '{persona_name}' not found. Available: {available}", False

    # Prevent sub-delegating to coordinators (that would be weird hierarchy)
    settings = persona_data.get('settings', {})
    persona_toolset = settings.get('toolset', '') or 'conversation'
    tool_names = []
    try:
        from core.toolsets import toolset_manager
        if persona_toolset == "all":
            tool_names = ['run_command', 'web_search']
        elif toolset_manager.toolset_exists(persona_toolset):
            tool_names = toolset_manager.get_toolset_functions(persona_toolset)
    except Exception:
        pass

    if 'delegate_task' in tool_names:
        return f"ERROR: Cannot sub-delegate to a coordinator ({persona_name}). Pick a specialist.", False

    chat_name = _get_active_chat()
    delegate_id = uuid.uuid4().hex[:8]

    delegate = PersonaDelegate(
        delegate_id=delegate_id,
        persona_name=persona_name,
        persona_data=persona_data,
        task=task,
        toolset=persona_toolset,
        context=f"(Sub-task from another specialist, depth={current_depth + 1})",
        chat_name=chat_name,
    )

    with _lock:
        _delegates[delegate_id] = delegate

    log_event("SUB-DELEGATE", f"Sub-delegation to {persona_name} (depth={current_depth + 1}): {task[:80]}")

    # Run inline (blocking) with reduced tool rounds
    original_execute = delegate._execute

    def _limited_execute():
        _SUB_DELEGATE_DEPTH.depth = current_depth + 1
        try:
            original_execute()
        finally:
            _SUB_DELEGATE_DEPTH.depth = current_depth

    delegate._execute = _limited_execute
    delegate.start()

    # Wait synchronously (sub-delegates should be quick)
    MAX_WAIT = 120  # 2 minute cap for sub-tasks
    poll_interval = 0.5
    waited = 0.0
    while delegate.status == 'running' and waited < MAX_WAIT:
        time.sleep(poll_interval)
        waited += poll_interval

    if delegate.status == 'running':
        delegate.cancel(force=True)
        return f"{delegate.display_name} timed out on sub-task (>{MAX_WAIT}s). Proceeding without their input.", True

    tools_used = ', '.join(delegate.tool_log) if delegate.tool_log else 'none'
    result = delegate.result or delegate.error or '(No result)'

    # Clean up — sub-delegates don't persist for continuation
    with _lock:
        _delegates.pop(delegate_id, None)

    status_icon = '\u2705' if delegate.status == 'done' else '\u274c'
    return (
        f"{status_icon} Sub-task [{delegate.display_name}] — {delegate.status} in {delegate.elapsed}s | tools: {tools_used}\n\n"
        f"{result}",
        True
    )


# ── Agent Lessons (Persistent Learning) ────────────────────────────────────

def _record_lesson(arguments):
    """Record a lesson learned by the currently running delegate."""
    lesson_text = arguments.get('lesson', '').strip()
    category = arguments.get('category', 'session').strip().lower()

    if not lesson_text:
        return "ERROR: lesson text is required.", False

    if category not in ('temporary', 'session', 'permanent'):
        category = 'session'

    # Figure out which persona is calling this — find the running delegate
    chat_name = _get_active_chat()
    persona_name = 'unknown'
    with _lock:
        for d in _delegates.values():
            if d.status == 'running' and d.chat_name == chat_name:
                persona_name = d.persona_name
                break

    _lessons_record(persona_name, lesson_text, category)

    ttl_label = {'temporary': '24 hours', 'session': '7 days', 'permanent': '90 days'}.get(category, '7 days')
    return f"Lesson recorded ({category}, expires in {ttl_label}). You'll see this next time you're activated.", True


def _contradict_lesson(arguments):
    """Mark a past lesson as wrong or outdated."""
    lesson_text = arguments.get('lesson', '').strip()

    if not lesson_text:
        return "ERROR: lesson text is required.", False

    chat_name = _get_active_chat()
    persona_name = 'unknown'
    with _lock:
        for d in _delegates.values():
            if d.status == 'running' and d.chat_name == chat_name:
                persona_name = d.persona_name
                break

    _lessons_contradict(persona_name, lesson_text)
    return f"Lesson weakened. If contradicted again it will be removed automatically.", True


def _write_agent_skills(arguments):
    """Create or update a persona's skills.md file."""
    from persona_agents_skills import get_skills, save_skills, generate_skills

    persona_name = arguments.get('persona_name', '').strip().lower()
    mode = arguments.get('mode', 'auto').strip().lower()
    content = arguments.get('content', '').strip()

    if not persona_name:
        return "ERROR: persona_name is required.", False

    # Validate persona exists
    persona_data = _load_persona(persona_name)
    if not persona_data:
        available = ', '.join(_list_all_personas())
        return f"ERROR: Persona '{persona_name}' not found. Available: {available}", False

    if mode == 'auto':
        # Auto-generate from toolset with frontmatter + approach patterns
        generated = generate_skills(persona_name)
        if not generated:
            return f"ERROR: Could not auto-generate skills for '{persona_name}'. They may be a chat-only persona.", False
        if save_skills(persona_name, generated):
            return f"Skills auto-generated for '{persona_name}' with role metadata, triggers, and approach patterns.", True
        return f"ERROR: Failed to save skills for '{persona_name}'.", False

    elif mode == 'manual':
        if not content:
            return "ERROR: 'content' is required for manual mode.", False
        if save_skills(persona_name, content):
            return f"Skills written for '{persona_name}' ({len(content)} chars).", True
        return f"ERROR: Failed to save skills for '{persona_name}'.", False

    elif mode == 'augment':
        if not content:
            return "ERROR: 'content' is required for augment mode.", False
        existing = get_skills(persona_name)
        if existing:
            combined = existing + "\n\n" + content
        else:
            combined = content
        if save_skills(persona_name, combined):
            action = "augmented" if existing else "created"
            return f"Skills {action} for '{persona_name}' ({len(combined)} chars total).", True
        return f"ERROR: Failed to save skills for '{persona_name}'.", False

    else:
        return f"ERROR: Unknown mode '{mode}'. Use 'auto', 'manual', or 'augment'.", False
