# delegation_log.py — Dedicated log file for persona-agent delegations
# Auto-prunes entries older than configured retention period
# Writes to user/logs/persona_agents.log with human-readable format

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────

LOG_DIR = Path("user/logs")
LOG_FILE = LOG_DIR / "persona_agents.log"
RETENTION_DAYS = 3          # Auto-prune entries older than this
MAX_LOG_SIZE_MB = 5         # If log exceeds this, prune oldest half
PRUNE_INTERVAL = 3600       # Check for pruning every hour (seconds)

_lock = threading.Lock()
_last_prune = 0


def _ensure_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _date_prefix():
    return datetime.now().strftime("%Y-%m-%d")


# ── Public API ──────────────────────────────────────────────────────────────

def log_dispatch(delegate_id: str, persona: str, display_name: str,
                 task: str, toolset: str, chat_name: str):
    """Log when a persona agent is dispatched."""
    line = (
        f"[{_timestamp()}] DISPATCH | id={delegate_id} | "
        f"persona={persona} ({display_name}) | toolset={toolset} | "
        f"chat={chat_name}\n"
        f"  └─ Task: {task}\n"
    )
    _write(line)


def log_tool_call(delegate_id: str, persona: str, tool_name: str,
                  args_summary: str = ""):
    """Log when a delegate uses a tool."""
    args_str = f" | args: {args_summary[:200]}" if args_summary else ""
    line = (
        f"[{_timestamp()}]   TOOL   | id={delegate_id} | "
        f"{persona} → {tool_name}{args_str}\n"
    )
    _write(line)


def log_result(delegate_id: str, persona: str, display_name: str,
               status: str, elapsed: float, tool_log: list,
               result_preview: str = "", error: str = ""):
    """Log when a delegate finishes (success or failure)."""
    tools_str = ", ".join(tool_log) if tool_log else "none"
    preview = result_preview[:300].replace("\n", " ") if result_preview else ""
    err_str = f"\n  └─ Error: {error}" if error else ""

    line = (
        f"[{_timestamp()}] RESULT  | id={delegate_id} | "
        f"persona={persona} ({display_name}) | status={status} | "
        f"elapsed={elapsed}s | tools=[{tools_str}]\n"
        f"  └─ Preview: {preview}{err_str}\n"
    )
    _write(line)


def log_batch_complete(chat_name: str, agent_count: int):
    """Log when all delegates for a chat finish."""
    line = (
        f"[{_timestamp()}] BATCH   | chat={chat_name} | "
        f"All {agent_count} delegate(s) complete\n"
        f"{'─' * 72}\n"
    )
    _write(line)


def log_event(event_type: str, message: str):
    """Log a generic event (plugin load, errors, etc.)."""
    line = f"[{_timestamp()}] {event_type:7s} | {message}\n"
    _write(line)


def log_prompt_inject(persona_count: int, active_persona: str = ""):
    """Log when the roster is injected into the prompt."""
    line = (
        f"[{_timestamp()}] INJECT  | Roster injected: {persona_count} personas "
        f"(active: {active_persona or 'unknown'})\n"
    )
    _write(line)


# ── Read / Tail ─────────────────────────────────────────────────────────────

def read_log(lines: int = 100) -> str:
    """Read the last N lines of the log. Useful for debugging."""
    try:
        if not LOG_FILE.exists():
            return "(no log file yet)"
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return "".join(tail)
    except Exception as e:
        return f"(error reading log: {e})"


def get_log_stats() -> dict:
    """Get log file stats."""
    try:
        if not LOG_FILE.exists():
            return {"exists": False}
        stat = LOG_FILE.stat()
        return {
            "exists": True,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "path": str(LOG_FILE),
        }
    except Exception as e:
        return {"exists": False, "error": str(e)}


# ── Internal ────────────────────────────────────────────────────────────────

def _write(text: str):
    """Thread-safe write to log file. Triggers prune check."""
    try:
        _ensure_dir()
        with _lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text)
    except Exception as e:
        logger.error(f"[PERSONA-AGENT-LOG] Write failed: {e}")

    # Periodic prune check (non-blocking)
    _maybe_prune()


def _maybe_prune():
    """Check if we should prune old entries. Runs at most once per PRUNE_INTERVAL."""
    global _last_prune
    now = time.time()
    if now - _last_prune < PRUNE_INTERVAL:
        return
    _last_prune = now

    # Run in background thread to not block
    threading.Thread(target=_prune, daemon=True, name="pa-log-prune").start()


def _prune():
    """Remove log entries older than RETENTION_DAYS, or trim if file too large."""
    try:
        if not LOG_FILE.exists():
            return

        stat = LOG_FILE.stat()
        size_mb = stat.st_size / (1024 * 1024)

        # Read all lines
        with _lock:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

        if not lines:
            return

        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        # Filter: keep lines whose timestamp is after cutoff
        # Lines starting with [ have timestamps, continuation lines (└─) keep with parent
        kept = []
        keep_current = True
        for line in lines:
            if line.startswith("["):
                # Extract timestamp from [YYYY-MM-DD HH:MM:SS]
                try:
                    ts_str = line[1:20]  # "2026-04-07 14:30:00"
                    keep_current = ts_str >= cutoff_str
                except (IndexError, ValueError):
                    keep_current = True  # Keep if we can't parse
            # Continuation lines follow parent's fate
            if keep_current:
                kept.append(line)

        # Also check size — if still too big after date prune, keep newest half
        if size_mb > MAX_LOG_SIZE_MB and len(kept) > 100:
            kept = kept[len(kept) // 2:]

        pruned_count = len(lines) - len(kept)

        if pruned_count > 0:
            with _lock:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write(f"[{_timestamp()}] PRUNE   | Removed {pruned_count} old lines "
                            f"(retention: {RETENTION_DAYS} days)\n")
                    f.writelines(kept)
            logger.info(f"[PERSONA-AGENT-LOG] Pruned {pruned_count} lines from log")

    except Exception as e:
        logger.error(f"[PERSONA-AGENT-LOG] Prune failed: {e}")
