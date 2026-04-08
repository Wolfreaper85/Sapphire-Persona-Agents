# plugins/persona-agents/tools/youtube_transcript.py
# YouTube transcript fetcher — gives agents access to actual video content
# Uses youtube-transcript-api to pull captions with timestamps.

import re
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎬'

MAX_TRANSCRIPT_CHARS = 12000

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
                "Fetch the transcript/captions of a YouTube video with timestamps. "
                "Works with any YouTube URL or video ID. Returns the full spoken content "
                "of the video so you can summarize, analyze, or quote it accurately. "
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


def _extract_youtube_id(url):
    """Extract video ID from various YouTube URL formats."""
    if not url:
        return None
    # Direct video ID (11 chars, no slashes/dots)
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


def _fetch_transcript(video_id, original_url=''):
    """Fetch YouTube transcript using youtube-transcript-api v1.2+."""
    from youtube_transcript_api import YouTubeTranscriptApi

    yt = YouTubeTranscriptApi()
    transcript = yt.fetch(video_id)
    entries = list(transcript)

    if not entries:
        return None

    lines = []
    lines.append(f"[YouTube Video Transcript — {original_url or video_id}]")
    lang_info = getattr(transcript, 'language', 'Unknown')
    is_gen = getattr(transcript, 'is_generated', None)
    lang_note = " (auto-generated)" if is_gen else ""
    lines.append(f"Language: {lang_info}{lang_note}")

    # Total duration from last entry
    last = entries[-1]
    total_secs = int(last.start + last.duration)
    total_mins = total_secs // 60
    total_secs_rem = total_secs % 60
    lines.append(f"Duration: ~{total_mins}:{total_secs_rem:02d}")
    lines.append("")

    for entry in entries:
        mins = int(entry.start) // 60
        secs = int(entry.start) % 60
        timestamp = f"[{mins}:{secs:02d}]"
        text = entry.text.replace('\n', ' ').strip()
        if text:
            lines.append(f"{timestamp} {text}")

    result = "\n".join(lines)
    logger.info(f"[YT-TRANSCRIPT] Got transcript for {video_id}: {len(entries)} entries, {len(result)} chars")
    return result


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

    logger.info(f"[YT-TRANSCRIPT] Fetching transcript for video_id={video_id} (url={url})")

    try:
        result = _fetch_transcript(video_id, url)
    except ImportError:
        return (
            "youtube-transcript-api is not installed. "
            "Install it with: pip install youtube-transcript-api",
            False
        )
    except Exception as e:
        logger.error(f"[YT-TRANSCRIPT] Failed for {video_id}: {e}")
        return (
            f"Could not fetch transcript for this video. Error: {e}\n"
            f"The video may not have captions/subtitles available, or it may be private/age-restricted.",
            False
        )

    if not result:
        return (
            "This video has no transcript/captions available. "
            "Cannot extract spoken content. Try searching for a written summary instead.",
            False
        )

    if len(result) > MAX_TRANSCRIPT_CHARS:
        result = result[:MAX_TRANSCRIPT_CHARS] + f"\n\n[Truncated to {MAX_TRANSCRIPT_CHARS} chars]"

    return result, True
