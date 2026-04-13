"""Proactive content discovery — the top of the research pipeline.

Two modes, used together:

AUTONOMOUS (default when specialty is set)
  Each cycle the LLM reads the agent's specialty + domains and generates
  fresh search queries. Those queries run against YouTube (Data API if
  NEO_YOUTUBE_API_KEY is set, otherwise falls back to Exa/Tavily scoped
  to youtube.com). New videos get their transcripts fetched and are stored
  as nodes. Spark generation fires automatically on ingestion.

CONFIGURED SOURCES (explicit subscriptions)
  For shows / channels you always want regardless of topic:
    youtube_channel   — polls the channel's public RSS feed
    youtube_playlist  — polls a playlist's RSS feed
    youtube_search    — persistent search query, re-run each cycle
    rss               — any RSS 2.0 / Atom feed

Configured sources are optional ingestion hints stored in agent.config["research_sources"]:
  {
    "type":             "youtube_channel" | "youtube_playlist" |
                        "youtube_search"  | "rss",
    "id":               channel/playlist ID  (YouTube feed types),
    "query":            search string        (youtube_search type),
    "url":              feed URL             (rss type),
    "name":             human-readable label,
    "domain":           domain tag for ingested nodes (optional),
    "parent_node_id":   optional topic parent override for ingested knowledge,
    "enabled":          bool (default True),
    "last_ingested_at": ISO timestamp — only content after this is fetched,
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

logger = logging.getLogger(__name__)

_YT_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "yt":    "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
_YT_CHANNEL_RSS  = "https://www.youtube.com/feeds/videos.xml?channel_id={id}"
_YT_PLAYLIST_RSS = "https://www.youtube.com/feeds/videos.xml?playlist_id={id}"
_MAX_SOURCE_TEXT_CHARS = 12000
_MAX_TITLE_WORDS = 12


# ── XML helpers ───────────────────────────────────────────────────────────────

async def _fetch_xml(url: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "NeoResearchBot/1.0"})
        resp.raise_for_status()
        return resp.text


# ── Source → knowledge extraction ─────────────────────────────────────────────

def _clean_source_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_titleish(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _source_like_title(candidate: str, source_title: str) -> bool:
    candidate_norm = _normalize_titleish(candidate)
    source_norm = _normalize_titleish(source_title)
    if not candidate_norm or not source_norm:
        return False
    return (
        candidate_norm == source_norm
        or (len(candidate_norm) > 16 and candidate_norm in source_norm)
        or (len(source_norm) > 16 and source_norm in candidate_norm)
    )


def _summarize_text(text: str, max_words: int = 28) -> str:
    words = _clean_source_text(text).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def _title_from_content(content: str, source_title: str, index: int) -> str:
    first_clause = re.split(r"(?<=[.!?])\s+|[;:]\s+", _clean_source_text(content), maxsplit=1)[0]
    words = first_clause.split()
    title = " ".join(words[:_MAX_TITLE_WORDS]).strip(" ,.-")
    if len(title) > 90:
        title = title[:87].rsplit(" ", 1)[0].strip(" ,.-") + "..."
    if not title or _source_like_title(title, source_title):
        title = f"Knowledge finding {index}"
    return title[:1].upper() + title[1:]


def _sentence_units(text: str) -> list[str]:
    cleaned = _clean_source_text(text)
    if not cleaned:
        return []
    units = [
        unit.strip()
        for unit in re.split(r"(?<=[.!?])\s+", cleaned)
        if len(unit.split()) >= 6
    ]
    if len(units) >= 2:
        return units

    words = cleaned.split()
    if len(words) < 18:
        return [cleaned] if cleaned else []
    chunk_size = 80
    return [
        " ".join(words[i:i + chunk_size])
        for i in range(0, min(len(words), chunk_size * 4), chunk_size)
        if len(words[i:i + chunk_size]) >= 12
    ]


def _fallback_findings(
    *,
    source_title: str,
    source_text: str,
    max_findings: int,
    confidence: float,
) -> list[dict[str, Any]]:
    units = _sentence_units(source_text)
    findings: list[dict[str, Any]] = []
    for unit in units[:max_findings]:
        index = len(findings) + 1
        findings.append({
            "title": _title_from_content(unit, source_title, index),
            "summary": _summarize_text(unit),
            "content": unit,
            "confidence": confidence,
        })
    return findings


def _validated_findings(
    raw_findings: Any,
    *,
    source_title: str,
    fallback_text: str,
    max_findings: int,
    confidence: float,
) -> list[dict[str, Any]]:
    if not isinstance(raw_findings, list):
        return []
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        content = _clean_source_text(str(raw.get("content") or ""))
        if len(content.split()) < 6:
            continue
        index = len(findings) + 1
        title = _clean_source_text(str(raw.get("title") or ""))
        if not title or _source_like_title(title, source_title):
            title = _title_from_content(content, source_title, index)
        summary = _clean_source_text(str(raw.get("summary") or "")) or _summarize_text(content)
        raw_confidence = raw.get("confidence", confidence)
        try:
            finding_confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            finding_confidence = confidence
        findings.append({
            "title": title[:90],
            "summary": summary[:240],
            "content": content,
            "confidence": finding_confidence,
        })
        if len(findings) >= max_findings:
            break
    if findings:
        return findings
    return _fallback_findings(
        source_title=source_title,
        source_text=fallback_text,
        max_findings=max_findings,
        confidence=confidence,
    )


async def extract_knowledge_findings(
    *,
    source_title: str,
    source_text: str,
    source_type: str,
    source_url: str = "",
    agent_focus: str = "",
    llm: Any | None = None,
    max_findings: int = 4,
    confidence: float = 0.55,
) -> list[dict[str, Any]]:
    """Extract durable knowledge findings from any source text.

    Source title and URL are provenance. Returned finding titles describe the
    knowledge itself and intentionally avoid mirroring the article/video title.
    """
    cleaned = _clean_source_text(source_text)
    if not cleaned:
        return []

    clipped = cleaned[:_MAX_SOURCE_TEXT_CHARS]
    if llm is not None:
        try:
            prompt = f"""Extract durable knowledge findings from this {source_type} source.

Source title: {source_title}
Source URL: {source_url or "unknown"}
Agent research focus: {agent_focus or "general durable knowledge"}

Rules:
- Return distinct learnings, not a summary of the source.
- Each finding title must describe the knowledge claim itself.
- Do not use the source title as a finding title.
- Prefer specific, reusable claims over episode/article framing.
- Return between 1 and {max_findings} findings.

Respond only as JSON:
[
  {{
    "title": "short claim title, max 80 characters",
    "summary": "one sentence summary",
    "content": "2-4 sentences preserving the useful knowledge",
    "confidence": 0.0
  }}
]

Source text:
{clipped}"""
            raw = await llm.call(prompt, max_tokens=1200)
            start, end = raw.find("["), raw.rfind("]") + 1
            if start >= 0 and end > start:
                parsed = json.loads(raw[start:end])
                findings = _validated_findings(
                    parsed,
                    source_title=source_title,
                    fallback_text=clipped,
                    max_findings=max_findings,
                    confidence=confidence,
                )
                if findings:
                    return findings
        except Exception as exc:
            logger.debug("LLM source finding extraction failed, using fallback: %s", exc)

    return _fallback_findings(
        source_title=source_title,
        source_text=clipped,
        max_findings=max_findings,
        confidence=confidence,
    )


def append_source_provenance(content: str, parts: list[str]) -> str:
    provenance = " | ".join(part for part in parts if part)
    if not provenance:
        return content
    return f"{content}\n\n---\n{provenance}"


def _parse_youtube_feed(xml_text: str, since: datetime | None) -> list[dict]:
    root = ET.fromstring(xml_text)
    entries: list[dict] = []
    for entry in root.findall("atom:entry", _YT_NS):
        vid_el   = entry.find("yt:videoId", _YT_NS)
        title_el = entry.find("atom:title", _YT_NS)
        pub_el   = entry.find("atom:published", _YT_NS)
        desc_el  = entry.find(".//media:description", _YT_NS)
        link_el  = entry.find("atom:link", _YT_NS)

        if vid_el is None or title_el is None:
            continue

        video_id = vid_el.text or ""
        title    = title_el.text or ""
        url      = (link_el.attrib.get("href") if link_el is not None else None) \
                   or f"https://www.youtube.com/watch?v={video_id}"
        description = (desc_el.text or "")[:500] if desc_el is not None else ""

        published_at: datetime | None = None
        if pub_el is not None and pub_el.text:
            try:
                published_at = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00"))
            except ValueError:
                pass

        if since and published_at and published_at <= since:
            continue

        entries.append({
            "video_id":     video_id,
            "title":        title,
            "url":          url,
            "description":  description,
            "published_at": published_at,
        })

    entries.sort(
        key=lambda e: e["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return entries


def _parse_rss_feed(xml_text: str, since: datetime | None) -> list[dict]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    items: list[dict] = []
    for item in (channel or root).findall("item"):
        title       = (item.findtext("title") or "").strip()
        url         = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()[:500]
        pub_str     = item.findtext("pubDate") or item.findtext("published") or ""

        published_at: datetime | None = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
                    "%Y-%m-%dT%H:%M:%S%z"):
            try:
                published_at = datetime.strptime(pub_str.strip(), fmt)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

        if since and published_at and published_at <= since:
            continue
        if not title or not url:
            continue

        items.append({"title": title, "url": url, "description": description, "published_at": published_at})

    items.sort(
        key=lambda e: e["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items


# ── Query generation ──────────────────────────────────────────────────────────

async def _generate_search_queries(
    specialty: str,
    domains: list[str],
    llm: Any | None = None,
    n: int = 4,
) -> list[str]:
    """Generate search queries from the agent's research direction.

    Uses the LLM when available for richer, more varied queries.
    Falls back to template-based generation so discovery works even
    without an LLM key configured.
    """
    if llm is not None:
        try:
            import json
            domain_str = ", ".join(domains) if domains else "general"
            prompt = f"""You are planning research for an AI agent with this focus:

{specialty}

Core domains: {domain_str}

Generate {n} YouTube search queries to find the best recent content on these topics.
Think like a researcher: what would a curious person actually search for this week?
Prefer queries that would surface long-form interviews, podcast episodes, and talks
(not explainer videos or tutorials). Vary the angles — don't repeat the same framing.

Respond with a JSON array of search strings only (6-12 words each):
["query one", "query two", ...]"""
            raw = await llm.call(prompt, max_tokens=300)
            start, end = raw.find("["), raw.rfind("]") + 1
            queries = json.loads(raw[start:end])
            valid = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
            if valid:
                return valid[:n]
        except Exception as exc:
            logger.debug("LLM query generation failed, using template: %s", exc)

    # Template fallback — combine domain tags with content-type modifiers
    content_types = ["interview", "podcast", "talk", "conversation"]
    queries: list[str] = []
    for domain in (domains or [])[:3]:
        domain_clean = domain.replace("-", " ")
        queries.append(f"{domain_clean} interview founder lessons")
        queries.append(f"best {domain_clean} podcast episode")
    # Pull key phrases from specialty (first sentence)
    if specialty:
        first_sentence = specialty.split(".")[0].strip()
        words = first_sentence.split()
        if len(words) >= 4:
            queries.append(" ".join(words[:6]))
    return queries[:n]


# ── Ingested-video tracking ───────────────────────────────────────────────────

async def _already_ingested(api: Any, agent_id: str, video_id: str) -> bool:
    """Check if a video was already stored as a node (by metadata.video_id)."""
    try:
        nodes = await api.store.get_nodes_by_agent(agent_id, limit=2000)
        return any(
            (n.get("metadata") or {}).get("video_id") == video_id
            for n in nodes
        )
    except Exception:
        return False


# ── Discovery job ─────────────────────────────────────────────────────────────

class DiscoveryJob:
    """Polls research sources and runs autonomous search to ingest new content.

    Call run() each cycle. It processes configured sources first, then
    runs the autonomous search pass if the agent has a specialty set.
    """

    def __init__(self, api: Any, llm: Any | None = None, yt_search: Any | None = None) -> None:
        self.api = api
        self.llm = llm          # optional: ResolutionLLM instance for query generation
        self.yt_search = yt_search  # optional: YouTubeSearchClient or EchoSearchAsYouTube

    async def run(
        self,
        agent: dict,
        batch_size: int = 5,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        config    = agent.get("config") or {}
        sources   = list(config.get("research_sources") or [])
        specialty = (agent.get("specialty") or "").strip()
        domains   = agent.get("domains") or []

        summary: dict[str, int] = {
            "ingested": 0, "skipped": 0, "errors": 0,
            "sources_checked": 0, "autonomous_queries": 0,
        }
        updated_sources: list[dict] = []

        # 1. Configured sources (channels, playlists, persistent queries, RSS)
        for source in sources:
            if not source.get("enabled", True):
                updated_sources.append(source)
                continue

            summary["sources_checked"] += 1
            source = dict(source)

            since = _parse_since(source.get("last_ingested_at"))
            # For source polling, look back further (don't miss episodes)
            effective_since = since or (
                datetime.now(timezone.utc) - timedelta(days=lookback_days)
            )

            try:
                ingested = await self._process_source(
                    agent, source, effective_since, batch_size, lookback_days
                )
                summary["ingested"] += ingested
                if ingested > 0:
                    source["last_ingested_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                logger.warning("Discovery: source '%s' failed: %s", source.get("name"), exc)
                summary["errors"] += 1

            updated_sources.append(source)

        # 2. Autonomous search — generate queries from specialty, search YouTube
        if specialty and self.yt_search is not None:
            try:
                auto_ingested = await self._autonomous_search(
                    agent, specialty, domains, batch_size, lookback_days
                )
                summary["ingested"] += auto_ingested
                summary["autonomous_queries"] += 1
            except Exception as exc:
                logger.warning("Discovery: autonomous search failed: %s", exc)
                summary["errors"] += 1
        elif specialty and self.yt_search is None:
            logger.info(
                "Discovery: specialty set but no YouTube search client — "
                "set NEO_YOUTUBE_API_KEY or NEO_SEARCH_API_KEY to enable autonomous search"
            )

        # Persist updated timestamps
        new_config = {**config, "research_sources": updated_sources}
        try:
            await self.api.store.update_agent(agent["id"], config=new_config)
        except Exception as exc:
            logger.warning("Discovery: failed to persist source timestamps: %s", exc)

        logger.info(
            "Discovery: %d ingested | %d sources | autonomous=%s",
            summary["ingested"], summary["sources_checked"],
            "yes" if summary["autonomous_queries"] else "no",
        )
        return summary

    # ── Autonomous search ─────────────────────────────────────────────────────

    async def _autonomous_search(
        self,
        agent: dict,
        specialty: str,
        domains: list[str],
        batch_size: int,
        lookback_days: int,
    ) -> int:
        suggested_sources = (agent.get("config") or {}).get("suggested_sources") or []
        query_specialty = specialty
        if suggested_sources:
            query_specialty = f"{specialty}\nSuggested sources: {', '.join(suggested_sources)}"
        queries = await _generate_search_queries(query_specialty, domains, llm=self.llm, n=4)
        logger.info("Discovery (autonomous): %d queries — %s", len(queries), queries)

        ingested = 0
        seen_this_cycle: set[str] = set()

        for query in queries:
            if ingested >= batch_size:
                break
            try:
                results = await self.yt_search.search(
                    query,
                    max_results=6,
                    published_after_days=lookback_days,
                    min_duration_seconds=180,
                )
            except Exception as exc:
                logger.warning("Discovery: search '%s' failed: %s", query[:50], exc)
                continue

            for result in results:
                if ingested >= batch_size:
                    break
                vid = result.get("video_id", "")
                if not vid or vid in seen_this_cycle:
                    continue
                if await _already_ingested(self.api, agent["id"], vid):
                    continue
                seen_this_cycle.add(vid)
                try:
                    await self._store_youtube_video(
                        agent,
                        video_id=vid,
                        title=result.get("title", vid),
                        url=result["url"],
                        description=result.get("description", ""),
                        channel_name=result.get("channel_title", ""),
                        published_at=result.get("published_at", ""),
                        domain=domains[0] if domains else None,
                        source_name=f"Autonomous: {query[:40]}",
                    )
                    ingested += 1
                    logger.info(
                        "Discovery (autonomous): ingested '%s'", result.get("title", vid)[:60]
                    )
                except Exception as exc:
                    logger.warning("Discovery: failed to store %s: %s", vid, exc)

        return ingested

    # ── Configured sources ────────────────────────────────────────────────────

    async def _process_source(
        self, agent, source, since, batch_size, lookback_days
    ) -> int:
        src_type = source.get("type", "")
        if src_type == "youtube_channel":
            feed_url = _YT_CHANNEL_RSS.format(id=source["id"])
            return await self._ingest_youtube_feed(agent, source, feed_url, since, batch_size)
        elif src_type == "youtube_playlist":
            feed_url = _YT_PLAYLIST_RSS.format(id=source["id"])
            return await self._ingest_youtube_feed(agent, source, feed_url, since, batch_size)
        elif src_type == "youtube_search":
            return await self._ingest_youtube_search_source(
                agent, source, batch_size, lookback_days
            )
        elif src_type == "rss":
            return await self._ingest_rss_feed(agent, source, since, batch_size)
        else:
            logger.warning("Discovery: unknown source type '%s'", src_type)
            return 0

    async def _ingest_youtube_feed(self, agent, source, feed_url, since, batch_size) -> int:
        name = source.get("name", feed_url)
        xml_text = await _fetch_xml(feed_url)
        entries = _parse_youtube_feed(xml_text, since)[:batch_size]
        if not entries:
            logger.info("Discovery: '%s' — no new videos", name)
            return 0
        logger.info("Discovery: '%s' — %d new video(s)", name, len(entries))
        ingested = 0
        for entry in entries:
            try:
                await self._store_youtube_video(
                    agent,
                    video_id=entry["video_id"],
                    title=entry["title"],
                    url=entry["url"],
                    description=entry.get("description", ""),
                    channel_name=source.get("name", ""),
                    published_at=entry["published_at"].isoformat() if entry.get("published_at") else "",
                    domain=source.get("domain"),
                    parent_id=source.get("parent_node_id") or (agent.get("config") or {}).get("root_node_id"),
                    source_name=source.get("name", ""),
                )
                ingested += 1
            except Exception as exc:
                logger.warning("Discovery: failed %s from '%s': %s", entry.get("video_id"), name, exc)
        return ingested

    async def _ingest_youtube_search_source(
        self, agent, source, batch_size, lookback_days
    ) -> int:
        """Persistent YouTube search query — re-run each cycle, ingest new results."""
        if self.yt_search is None:
            logger.info("Discovery: youtube_search source '%s' skipped — no search client", source.get("name"))
            return 0
        query = source.get("query", "")
        if not query:
            return 0
        name = source.get("name", query)
        try:
            results = await self.yt_search.search(
                query,
                max_results=batch_size * 2,
                published_after_days=lookback_days,
                min_duration_seconds=180,
            )
        except Exception as exc:
            logger.warning("Discovery: search source '%s' failed: %s", name, exc)
            return 0

        ingested = 0
        for result in results:
            if ingested >= batch_size:
                break
            vid = result.get("video_id", "")
            if not vid or await _already_ingested(self.api, agent["id"], vid):
                continue
            try:
                await self._store_youtube_video(
                    agent,
                    video_id=vid,
                    title=result.get("title", vid),
                    url=result["url"],
                    description=result.get("description", ""),
                    channel_name=result.get("channel_title", ""),
                    published_at=result.get("published_at", ""),
                    domain=source.get("domain"),
                    parent_id=source.get("parent_node_id") or (agent.get("config") or {}).get("root_node_id"),
                    source_name=name,
                )
                ingested += 1
            except Exception as exc:
                logger.warning("Discovery: failed to store %s: %s", vid, exc)
        return ingested

    async def _ingest_rss_feed(self, agent, source, since, batch_size) -> int:
        name = source.get("name", source.get("url", "rss"))
        xml_text = await _fetch_xml(source["url"])
        items = _parse_rss_feed(xml_text, since)[:batch_size]
        if not items:
            logger.info("Discovery: '%s' — no new items", name)
            return 0
        logger.info("Discovery: '%s' — %d new item(s)", name, len(items))
        ingested = 0
        for item in items:
            try:
                await self._store_rss_item(agent, source, item)
                ingested += 1
            except Exception as exc:
                logger.warning("Discovery: failed '%s' from '%s': %s", item.get("title"), name, exc)
        return ingested

    # ── Node storage ──────────────────────────────────────────────────────────

    async def _store_youtube_video(
        self,
        agent: dict,
        *,
        video_id: str,
        title: str,
        url: str,
        description: str = "",
        channel_name: str = "",
        published_at: str = "",
        domain: str | None = None,
        parent_id: str | None = None,
        source_name: str = "",
    ) -> list[dict[str, Any]]:
        specialty = (agent.get("specialty") or "").strip()
        source_text: str = ""
        confidence: float = 0.6

        try:
            from neo.core.youtube import get_fetcher, extract_relevant_excerpt
            fetcher = get_fetcher()
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: fetcher.fetch(video_id))
            full_text = data["text"]
            if len(full_text) > _MAX_SOURCE_TEXT_CHARS:
                opening = " ".join(full_text.split()[:120])
                relevant = extract_relevant_excerpt(
                    full_text,
                    specialty or title,
                    max_chars=_MAX_SOURCE_TEXT_CHARS - len(opening) - 16,
                )
                source_text = f"{opening}\n\n{relevant}"
            else:
                source_text = full_text
        except Exception as exc:
            logger.info("Discovery: no transcript for %s (%s) — using description", video_id, exc)
            source_text = description or title
            confidence = 0.5

        findings = await extract_knowledge_findings(
            source_title=title,
            source_text=source_text,
            source_type="youtube",
            source_url=url,
            agent_focus=specialty,
            llm=self.llm,
            max_findings=4,
            confidence=confidence,
        )
        if not findings:
            return []

        provenance_parts = [f"Source: {url}"]
        if channel_name:
            provenance_parts.append(f"Channel: {channel_name}")
        if published_at:
            provenance_parts.append(f"Published: {published_at[:10]}")
        if source_name:
            provenance_parts.append(f"Via: {source_name}")

        results: list[dict[str, Any]] = []
        for index, finding in enumerate(findings, start=1):
            content = append_source_provenance(finding["content"], provenance_parts)
            result = await self.api.store_node(
                agent_id=agent["id"],
                node_type="finding",
                title=finding["title"],
                content=content,
                summary=finding["summary"],
                confidence=finding["confidence"],
                parent_id=parent_id,
                domain=domain,
                metadata={
                    "source_type":  "youtube",
                    "video_id":     video_id,
                    "source_title":  title,
                    "url":          url,
                    "channel_name": channel_name,
                    "published_at": published_at,
                    "finding_index": index,
                    "findings_total": len(findings),
                },
                generate_sparks=True,
                deduplicate=True,
            )
            results.append(result)
        return results

    async def _store_rss_item(self, agent, source, item) -> list[dict[str, Any]]:
        title   = item["title"]
        url     = item["url"]
        domain  = source.get("domain")
        pub_str = item["published_at"].strftime("%Y-%m-%d") if item.get("published_at") else ""
        findings = await extract_knowledge_findings(
            source_title=title,
            source_text=item.get("description") or title,
            source_type="rss",
            source_url=url,
            agent_focus=(agent.get("specialty") or "").strip(),
            llm=self.llm,
            max_findings=3,
            confidence=0.5,
        )
        if not findings:
            return []

        provenance_parts = [f"Source: {url}", f"Feed: {source.get('name', '')}"]
        if pub_str:
            provenance_parts.append(f"Published: {pub_str}")

        results: list[dict[str, Any]] = []
        for index, finding in enumerate(findings, start=1):
            content = append_source_provenance(finding["content"], provenance_parts)
            result = await self.api.store_node(
                agent_id=agent["id"],
                node_type="finding",
                title=finding["title"],
                content=content,
                summary=finding["summary"],
                confidence=finding["confidence"],
                parent_id=source.get("parent_node_id") or (agent.get("config") or {}).get("root_node_id"),
                domain=domain,
                metadata={
                    "source_type": "rss",
                    "source_title": title,
                    "url":          url,
                    "feed_name":    source.get("name", ""),
                    "published_at": item["published_at"].isoformat() if item.get("published_at") else None,
                    "finding_index": index,
                    "findings_total": len(findings),
                },
                generate_sparks=True,
                deduplicate=True,
            )
            results.append(result)
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_since(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
