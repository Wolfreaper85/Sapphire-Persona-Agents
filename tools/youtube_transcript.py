# plugins/persona-agents/tools/youtube_transcript.py
# YouTube transcript fetcher — gives agents access to actual video content.
#
# Hybrid approach:
#   - YouTube Data API v3 (official, API key) for rich metadata
#   - youtube-transcript-api package for caption/transcript text
#
# API key stored in Sapphire credentials manager (not hardcoded).

import re
import json
import logging
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎬'

MAX_TRANSCRIPT_CHARS = 12000
CRED_SERVICE_NAME = 'youtube_data_api'

AVAILABLE_FUNCTIONS = [
    'get_youtube_transcript',
]

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "get_youtube_transcript",
            "description": (
                "Fetch the transcript/captions and metadata of a YouTube video. "
                "Returns video title, channel, views, duration, and the full spoken "
                "content with timestamps. Works with any YouTube URL or video ID. "
                "Use this INSTEAD of get_website for YouTube links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "YouTube URL (e.g. https://www.youtube.com/watch?v=abc123) or video ID"
                    }
                },
                "required": ["url"]
            }
        }
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────

def _get_api_key():
    """Load YouTube Data API key from Sapphire credentials manager."""
    try:
        from core.credentials_manager import credentials
        key = credentials.get_service_api_key(CRED_SERVICE_NAME)
        if key:
            return key
    except Exception:
        pass
    import os
    return os.environ.get('YOUTUBE_API_KEY', '')


def _extract_youtube_id(url):
    """Extract video ID from various YouTube URL formats."""
    if not url:
        return None
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return url.strip()
    patterns = [
        r'(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _parse_duration(iso_dur):
    """Convert ISO 8601 duration (PT1H2M3S) to human-readable string."""
    if not iso_dur:
        return ''
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_dur)
    if not m:
        return iso_dur
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    if h > 0:
        return f"{h}:{mins:02d}:{s:02d}"
    return f"{mins}:{s:02d}"


# ─── YouTube Data API v3 — official metadata ─────────────────────────

def _fetch_metadata(video_id):
    """Fetch video metadata via YouTube Data API v3 (uses API key)."""
    api_key = _get_api_key()
    if not api_key:
        logger.info("[YT-TRANSCRIPT] No YouTube API key — skipping metadata")
        return None

    params = urlencode({
        'part': 'snippet,contentDetails,statistics',
        'id': video_id,
        'key': api_key,
    })
    api_url = f'https://www.googleapis.com/youtube/v3/videos?{params}'

    req = Request(api_url, headers={'User-Agent': 'Sapphire/1.0'})
    resp = urlopen(req, timeout=12)
    data = json.loads(resp.read().decode())

    items = data.get('items', [])
    if not items:
        return None

    item = items[0]
    snippet = item.get('snippet', {})
    stats = item.get('statistics', {})
    content = item.get('contentDetails', {})

    return {
        'title': snippet.get('title', 'Unknown'),
        'channel': snippet.get('channelTitle', 'Unknown'),
        'published': snippet.get('publishedAt', '')[:10],
        'description': snippet.get('description', ''),
        'duration': _parse_duration(content.get('duration', '')),
        'views': stats.get('viewCount', '0'),
        'likes': stats.get('likeCount', '0'),
    }


# ─── youtube-transcript-api — caption text ───────────────────────────

def _fetch_transcript(video_id, original_url=''):
    """Fetch transcript using youtube-transcript-api package."""
    from youtube_transcript_api import YouTubeTranscriptApi

    yt = YouTubeTranscriptApi()
    transcript = yt.fetch(video_id)
    entries = list(transcript)

    if not entries:
        return None, None

    lang_info = getattr(transcript, 'language', 'Unknown')
    is_gen = getattr(transcript, 'is_generated', None)

    caption_info = {
        'language': str(lang_info),
        'auto_generated': bool(is_gen),
    }

    # Convert to (start, text) tuples
    result_entries = []
    for entry in entries:
        text = entry.text.replace('\n', ' ').strip()
        if text:
            result_entries.append((float(entry.start), text))

    logger.info(
        f"[YT-TRANSCRIPT] Got transcript for {video_id}: "
        f"{len(result_entries)} entries, lang={lang_info}"
    )
    return result_entries, caption_info


# ─── Result builder ────────────────────────────────��─────────────────

def _build_result(video_id, metadata, captions, caption_info, original_url=''):
    """Assemble the final output string."""
    lines = []
    url_display = original_url or f'https://www.youtube.com/watch?v={video_id}'

    # Metadata header (from official YouTube Data API)
    if metadata:
        lines.append(f"[YouTube Video - {url_display}]")
        lines.append(f"Title: {metadata['title']}")
        lines.append(f"Channel: {metadata['channel']}")
        lines.append(f"Published: {metadata['published']}")
        views = int(metadata['views']) if metadata['views'] else 0
        likes = int(metadata['likes']) if metadata['likes'] else 0
        lines.append(f"Views: {views:,} | Likes: {likes:,}")
        if metadata['duration']:
            lines.append(f"Duration: {metadata['duration']}")
        lines.append("")
    else:
        lines.append(f"[YouTube Video - {url_display}]")
        lines.append("")

    # Transcript (from youtube-transcript-api)
    if captions:
        lang = caption_info.get('language', 'en') if caption_info else 'en'
        auto = " (auto-generated)" if caption_info and caption_info.get('auto_generated') else ""
        lines.append(f"=== Transcript [{lang}{auto}] ===")
        lines.append("")
        for start, text in captions:
            mins = int(start) // 60
            secs = int(start) % 60
            lines.append(f"[{mins}:{secs:02d}] {text}")
    elif metadata and metadata.get('description'):
        lines.append("[No captions available - showing video description instead]")
        lines.append("")
        desc = metadata['description']
        if len(desc) > 3000:
            desc = desc[:3000] + "\n[Description truncated]"
        lines.append(desc)
    else:
        lines.append("[No captions or description available for this video]")

    return "\n".join(lines)


# ─── Main execute ─────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    if function_name != "get_youtube_transcript":
        return f"Unknown function: {function_name}", False

    url = arguments.get('url', '').strip()
    if not url:
        return "I need a YouTube URL or video ID.", False

    video_id = _extract_youtube_id(url)
    if not video_id:
        return (
            f"Could not extract a YouTube video ID from '{url}'. "
            f"Please provide a full YouTube URL (e.g. https://www.youtube.com/watch?v=abc123) "
            f"or just the 11-character video ID.",
            False
        )

    logger.info(f"[YT-TRANSCRIPT] Fetching data for video_id={video_id} (url={url})")

    # 1. Metadata via official YouTube Data API v3
    metadata = None
    try:
        metadata = _fetch_metadata(video_id)
        if metadata:
            logger.info(f"[YT-TRANSCRIPT] Metadata OK: \"{metadata['title']}\" by {metadata['channel']}")
    except Exception as e:
        logger.warning(f"[YT-TRANSCRIPT] Metadata failed for {video_id}: {e}")

    # 2. Transcript via youtube-transcript-api
    captions = None
    caption_info = None
    try:
        captions, caption_info = _fetch_transcript(video_id, url)
    except ImportError:
        logger.error("[YT-TRANSCRIPT] youtube-transcript-api not installed! pip install youtube-transcript-api")
    except Exception as e:
        logger.warning(f"[YT-TRANSCRIPT] Transcript failed for {video_id}: {e}")

    if not metadata and not captions:
        return (
            f"Could not fetch any data for video '{video_id}'. "
            f"The video may be private, age-restricted, or the ID may be incorrect.",
            False
        )

    result = _build_result(video_id, metadata, captions, caption_info, url)

    if not result.strip():
        return "No transcript or metadata available for this video.", False

    entry_count = len(captions) if captions else 0
    logger.info(f"[YT-TRANSCRIPT] Complete for {video_id}: {entry_count} caption entries, {len(result)} chars")

    if len(result) > MAX_TRANSCRIPT_CHARS:
        result = result[:MAX_TRANSCRIPT_CHARS] + f"\n\n[Truncated to {MAX_TRANSCRIPT_CHARS} chars]"

    return result, True
