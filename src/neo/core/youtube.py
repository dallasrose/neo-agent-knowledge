"""YouTube transcript fetcher and relevance chunker.

Fetches transcripts for YouTube videos and extracts the most relevant
excerpts for a given query. Used by the spark resolver and the
ingest_youtube MCP tool.

No API key required — uses publicly available auto-generated captions.
Compatible with youtube-transcript-api >= 0.6 (instance-based API).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Matches: youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID,
#          youtube.com/embed/ID, youtube.com/v/ID
_YT_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?(?:[^&]+&)*v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/(?:shorts|embed|v)/([A-Za-z0-9_-]{11})"),
]


def extract_video_id(url: str) -> str | None:
    """Return the 11-char video ID from any YouTube URL, or None."""
    for pattern in _YT_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def is_youtube_url(url: str) -> bool:
    return any(p.search(url) for p in _YT_PATTERNS)


def _chunk_transcript(text: str, chunk_words: int = 150) -> list[str]:
    """Split a transcript into overlapping word-level chunks."""
    words = text.split()
    step = chunk_words // 2  # 50% overlap
    chunks = []
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_words])
        if chunk:
            chunks.append(chunk)
    return chunks


def _query_words(query: str) -> set[str]:
    """Return significant lower-case words from a query (skip stop-words)."""
    STOP = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "to", "of", "and", "or", "in", "on", "at", "for", "with",
        "this", "that", "it", "its", "by", "from", "as", "what",
        "how", "why", "when", "where", "who", "which", "do", "does",
    }
    words = re.findall(r"[a-z]{3,}", query.lower())
    return {w for w in words if w not in STOP}


def _score_chunk(chunk: str, query_words: set[str]) -> float:
    """Simple keyword-overlap score — fraction of query words present in chunk."""
    if not query_words:
        return 0.0
    chunk_lower = chunk.lower()
    hits = sum(1 for w in query_words if w in chunk_lower)
    return hits / len(query_words)


def extract_relevant_excerpt(text: str, query: str, max_chars: int = 1200) -> str:
    """Return the most query-relevant portion of a transcript (≤ max_chars)."""
    if len(text) <= max_chars:
        return text.strip()

    qw = _query_words(query)
    chunks = _chunk_transcript(text, chunk_words=150)
    if not chunks:
        return text[:max_chars].strip()

    scored = sorted(enumerate(chunks), key=lambda t: _score_chunk(t[1], qw), reverse=True)
    # Take top 3 chunks, re-sort by original position, stitch with ellipsis
    top_indices = sorted(idx for idx, _ in scored[:3])
    excerpt = " … ".join(chunks[i] for i in top_indices)
    return excerpt[:max_chars].strip()


class YouTubeTranscriptFetcher:
    """Fetch YouTube transcripts with graceful fallback.

    Uses youtube-transcript-api >= 0.6 instance-based API.
    """

    def fetch(self, video_id: str, languages: list[str] | None = None) -> dict[str, Any]:
        """Return {"text": str, "language": str, "duration_seconds": float | None}.

        Raises RuntimeError if no transcript is available.
        Requires: youtube-transcript-api, included by default.
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
        except ImportError:
            raise RuntimeError(
                "youtube-transcript-api is not installed. Reinstall or upgrade "
                "neo-agent-knowledge; it is included by default."
            )

        langs = languages or ["en", "en-US", "en-GB"]
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)

            # Prefer manual captions; fall back to auto-generated; fall back to any+translate
            transcript = None
            try:
                transcript = transcript_list.find_manually_created_transcript(langs)
            except NoTranscriptFound:
                pass

            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript(langs)
                except NoTranscriptFound:
                    pass

            if transcript is None:
                # Try any available language and translate to English
                available = list(transcript_list)
                if not available:
                    raise RuntimeError(f"No transcripts available for {video_id}")
                transcript = available[0].translate("en")

            fetched = transcript.fetch()

            # FetchedTranscript (>= 0.6) is iterable — items have .text and .start attributes
            snippets: list[str] = []
            last_start: float | None = None
            for snippet in fetched:
                try:
                    # Attribute-access style (>= 0.6)
                    snippets.append(snippet.text)
                    last_start = snippet.start
                except AttributeError:
                    # Dict-access style (< 0.6 fallback)
                    snippets.append(snippet["text"])
                    last_start = snippet.get("start")

            full_text = " ".join(snippets)
            # Strip [Music], [Applause], [Laughter], etc.
            full_text = re.sub(r"\[[\w\s]+\]", "", full_text)
            full_text = re.sub(r"\s{2,}", " ", full_text).strip()

            return {
                "text": full_text,
                "language": transcript.language_code,
                "duration_seconds": last_start,
                "video_id": video_id,
            }

        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not fetch transcript for {video_id}: {exc}") from exc

    def fetch_url(self, url: str, **kwargs) -> dict[str, Any]:
        vid = extract_video_id(url)
        if not vid:
            raise ValueError(f"Not a YouTube URL: {url}")
        result = self.fetch(vid, **kwargs)
        result["url"] = url
        return result

    def fetch_relevant_excerpt(self, video_id_or_url: str, query: str, max_chars: int = 1200) -> dict[str, Any]:
        """Fetch transcript and return only the most relevant excerpt."""
        if is_youtube_url(video_id_or_url):
            data = self.fetch_url(video_id_or_url)
        else:
            data = self.fetch(video_id_or_url)
        excerpt = extract_relevant_excerpt(data["text"], query, max_chars=max_chars)
        return {
            **data,
            "excerpt": excerpt,
            "full_length_chars": len(data["text"]),
        }


# ── YouTube Data API search ───────────────────────────────────────────────────

_YT_SEARCH_URL  = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS_URL  = "https://www.googleapis.com/youtube/v3/videos"

# ISO 8601 duration regex — used to detect Shorts (< ~3 min)
_DURATION_RE = re.compile(
    r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE
)


def _parse_duration_seconds(iso: str) -> int:
    m = _DURATION_RE.match(iso or "")
    if not m:
        return 0
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


class YouTubeSearchClient:
    """Search YouTube via the Data API v3.

    Requires: NEO_YOUTUBE_API_KEY (free, 10k units/day).
    search.list = 100 units per call → ~100 searches/day on free tier.

    Automatically filters out Shorts (< 3 minutes) so only substantive
    content is surfaced.
    """

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 10,
        published_after_days: int = 30,
        min_duration_seconds: int = 180,  # exclude Shorts
    ) -> list[dict[str, Any]]:
        """Return a list of video dicts ordered by YouTube's relevance ranking.

        Each dict: {video_id, title, description, channel_title, published_at, url}
        Duration filtering is applied via a second videos.list call.
        """
        import httpx
        from datetime import datetime, timedelta, timezone

        after = (
            datetime.now(timezone.utc) - timedelta(days=published_after_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Search — 100 units
            resp = await client.get(
                _YT_SEARCH_URL,
                params={
                    "key":           self._key,
                    "q":             query,
                    "type":          "video",
                    "part":          "snippet",
                    "maxResults":    min(max_results * 2, 50),  # over-fetch, filter by duration
                    "order":         "relevance",
                    "publishedAfter": after,
                    "videoEmbeddable": "true",
                },
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])

            if not items:
                return []

            video_ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]

            # 2. Get durations — 1 unit per 50 video IDs
            durations: dict[str, int] = {}
            if video_ids and min_duration_seconds > 0:
                dr = await client.get(
                    _YT_VIDEOS_URL,
                    params={
                        "key":  self._key,
                        "id":   ",".join(video_ids),
                        "part": "contentDetails",
                    },
                )
                dr.raise_for_status()
                for item in dr.json().get("items", []):
                    vid = item["id"]
                    dur_iso = item.get("contentDetails", {}).get("duration", "")
                    durations[vid] = _parse_duration_seconds(dur_iso)

        results: list[dict] = []
        for it in items:
            vid = it.get("id", {}).get("videoId", "")
            if not vid:
                continue
            dur = durations.get(vid, 9999)
            if dur < min_duration_seconds:
                continue  # skip Shorts and very short clips
            snip = it.get("snippet", {})
            results.append({
                "video_id":      vid,
                "title":         snip.get("title", ""),
                "description":   (snip.get("description") or "")[:400],
                "channel_title": snip.get("channelTitle", ""),
                "published_at":  snip.get("publishedAt", ""),
                "url":           f"https://www.youtube.com/watch?v={vid}",
                "duration_seconds": dur,
            })
            if len(results) >= max_results:
                break

        return results


class EchoSearchAsYouTube:
    """Fallback: use Exa/Tavily web search scoped to youtube.com.

    Less precise than the Data API (no duration filtering, no view-count
    signals) but works without a YouTube API key.
    """

    def __init__(self, web_search: Any) -> None:
        self._ws = web_search

    async def search(
        self,
        query: str,
        max_results: int = 10,
        published_after_days: int = 30,
        min_duration_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        yt_query = f"{query} site:youtube.com"
        try:
            raw = await self._ws.search(yt_query, max_results=max_results, days=published_after_days)
        except TypeError:
            raw = await self._ws.search(yt_query, max_results=max_results)

        results: list[dict] = []
        for r in raw:
            url = r.get("url", "")
            vid = extract_video_id(url)
            if not vid:
                continue
            results.append({
                "video_id":      vid,
                "title":         r.get("title", ""),
                "description":   r.get("snippet", "")[:400],
                "channel_title": "",
                "published_at":  r.get("published", ""),
                "url":           url,
                "duration_seconds": None,
            })
        return results


# ── Module-level singletons — lazy-initialised ────────────────────────────────

_fetcher: YouTubeTranscriptFetcher | None = None


def get_fetcher() -> YouTubeTranscriptFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = YouTubeTranscriptFetcher()
    return _fetcher
