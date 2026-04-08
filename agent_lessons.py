# Agent Lessons — Persistent learning for persona delegates
#
# After each delegation, the delegate can record short lessons:
#   "site X requires auth header"
#   "pip on this machine needs --user flag"
#   "tandem browser times out on JS-heavy sites"
#
# Before each delegation, recent lessons for that persona are loaded
# into the delegation prompt so the agent starts with accumulated knowledge.
#
# Staleness system:
#   - Each lesson has a category that determines its TTL:
#     * "temporary"  — 24 hours  (site down, service outage, transient errors)
#     * "session"    — 7 days    (workarounds, current project context)
#     * "permanent"  — 90 days   (system config, tool quirks, user preferences)
#   - Lessons can be reinforced (re-encountered = bump timestamp, raise confidence)
#   - Low-confidence lessons are pruned first when the list gets long

import json
import logging
import time
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent.parent.parent / 'user' / 'plugin_state' / 'persona-lessons.json'
_lock = Lock()
_lessons = {}  # persona_name -> [lesson_dict, ...]

# TTL in seconds per category
_TTL = {
    'temporary': 86400,       # 24 hours
    'session':   604800,      # 7 days
    'permanent': 7776000,     # 90 days
}

MAX_LESSONS_PER_PERSONA = 25   # Hard cap — oldest/lowest-confidence pruned beyond this
MAX_LESSONS_IN_PROMPT = 8      # How many to inject into delegation prompt (keep it focused)


def _load():
    """Load lessons from disk."""
    global _lessons
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding='utf-8'))
            _lessons = data.get('lessons', {})
            total = sum(len(v) for v in _lessons.values())
            logger.info(f"[AGENT-LESSONS] Loaded {total} lessons for {len(_lessons)} personas")
    except Exception as e:
        logger.warning(f"[AGENT-LESSONS] Failed to load: {e}")
        _lessons = {}


def _save():
    """Persist lessons to disk."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            data = {'lessons': _lessons}
        _STATE_FILE.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
    except Exception as e:
        logger.debug(f"[AGENT-LESSONS] Failed to save: {e}")


def _prune_expired(persona_name):
    """Remove expired lessons for a persona. Called before reads and writes."""
    if persona_name not in _lessons:
        return
    now = time.time()
    _lessons[persona_name] = [
        l for l in _lessons[persona_name]
        if now - l.get('timestamp', 0) < _TTL.get(l.get('category', 'session'), _TTL['session'])
    ]


def _prune_overflow(persona_name):
    """If a persona has too many lessons, drop the weakest ones."""
    if persona_name not in _lessons:
        return
    entries = _lessons[persona_name]
    if len(entries) <= MAX_LESSONS_PER_PERSONA:
        return
    # Sort by confidence (ascending), then age (oldest first) — weakest at top
    entries.sort(key=lambda l: (l.get('confidence', 1), l.get('timestamp', 0)))
    # Keep only the top N
    _lessons[persona_name] = entries[-MAX_LESSONS_PER_PERSONA:]


def record_lesson(persona_name, lesson_text, category='session'):
    """Record a lesson learned by a persona after a delegation.

    Args:
        persona_name: Which persona learned this
        lesson_text: Short description of what was learned (1-2 sentences max)
        category: 'temporary' (24h), 'session' (7d), or 'permanent' (90d)
    """
    persona_name = persona_name.lower().strip()
    lesson_text = lesson_text.strip()
    if not lesson_text or not persona_name:
        return

    if category not in _TTL:
        category = 'session'

    with _lock:
        if persona_name not in _lessons:
            _lessons[persona_name] = []

        _prune_expired(persona_name)

        # Check for duplicate / reinforcement — if a similar lesson already exists,
        # bump its confidence and timestamp instead of adding a new one
        for existing in _lessons[persona_name]:
            if _is_similar(existing.get('text', ''), lesson_text):
                existing['confidence'] = min(existing.get('confidence', 1) + 1, 5)
                existing['timestamp'] = time.time()
                existing['reinforced'] = existing.get('reinforced', 0) + 1
                # Upgrade category if the new one is more permanent
                cat_rank = {'temporary': 0, 'session': 1, 'permanent': 2}
                if cat_rank.get(category, 1) > cat_rank.get(existing.get('category', 'session'), 1):
                    existing['category'] = category
                logger.info(f"[AGENT-LESSONS] Reinforced lesson for {persona_name}: "
                           f"confidence={existing['confidence']}, text={lesson_text[:60]}")
                _save()
                return

        # New lesson
        entry = {
            'text': lesson_text,
            'category': category,
            'confidence': 1,
            'timestamp': time.time(),
            'reinforced': 0,
        }
        _lessons[persona_name].append(entry)
        _prune_overflow(persona_name)

    logger.info(f"[AGENT-LESSONS] New lesson for {persona_name} ({category}): {lesson_text[:80]}")
    _save()


def get_lessons_for_prompt(persona_name):
    """Get lessons to inject into a persona's delegation prompt.

    Returns a formatted string block, or empty string if no lessons.
    """
    persona_name = persona_name.lower().strip()

    with _lock:
        if persona_name not in _lessons:
            return ""

        _prune_expired(persona_name)
        entries = _lessons[persona_name]

    if not entries:
        return ""

    # Sort by confidence desc, then recency desc — best lessons first
    ranked = sorted(entries, key=lambda l: (l.get('confidence', 1), l.get('timestamp', 0)), reverse=True)
    top = ranked[:MAX_LESSONS_IN_PROMPT]

    lines = ["[Your Past Experience — lessons from previous tasks]",
             "IMPORTANT: Review these before starting. They are things YOU learned from past work."]
    for l in top:
        cat = l.get('category', 'session')
        conf = l.get('confidence', 1)
        # Show confidence as stars for easy reading
        stars = '★' * min(conf, 5)
        text = l.get('text', '')
        lines.append(f"  {stars} {text}  [{cat}]")

    lines.append("")
    lines.append("Apply these lessons to your current task. More ★ = more reliable.")
    lines.append("If you discover a lesson is WRONG (situation changed), call contradict_lesson to correct it.")
    return "\n".join(lines)


def contradict_lesson(persona_name, lesson_text):
    """Lower confidence of a lesson that turned out to be wrong.

    If the agent discovers contradicting evidence (site is back up, tool works now),
    this weakens the old lesson. At confidence 0, it's removed.
    """
    persona_name = persona_name.lower().strip()

    with _lock:
        if persona_name not in _lessons:
            return

        for existing in _lessons[persona_name]:
            if _is_similar(existing.get('text', ''), lesson_text):
                existing['confidence'] = max(existing.get('confidence', 1) - 1, 0)
                if existing['confidence'] <= 0:
                    _lessons[persona_name].remove(existing)
                    logger.info(f"[AGENT-LESSONS] Removed contradicted lesson for {persona_name}: {lesson_text[:60]}")
                else:
                    logger.info(f"[AGENT-LESSONS] Weakened lesson for {persona_name}: "
                               f"confidence={existing['confidence']}, text={lesson_text[:60]}")
                break

    _save()


def get_all_lessons():
    """Get all lessons for all personas (for admin/debug view)."""
    with _lock:
        # Prune all before returning
        for name in list(_lessons.keys()):
            _prune_expired(name)
        return {k: list(v) for k, v in _lessons.items()}


def clear_lessons(persona_name=None):
    """Clear lessons. If persona_name given, clear only that persona. Otherwise clear all."""
    with _lock:
        if persona_name:
            _lessons.pop(persona_name.lower().strip(), None)
        else:
            _lessons.clear()
    _save()


def _is_similar(text_a, text_b):
    """Simple similarity check — are these roughly the same lesson?

    Uses word overlap ratio. If >60% of words match, it's the same lesson.
    Not perfect, but good enough for dedup without pulling in ML libraries.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return (overlap / smaller) > 0.6 if smaller > 0 else False


# Load on import
_load()
