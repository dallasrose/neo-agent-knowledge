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


class IngestionProviderError(RuntimeError):
    """Raised when durable source ingestion cannot safely use the configured LLM."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


_PROVIDER_FATAL_STATUSES = {401, 403, 404, 429}
_PROVIDER_FATAL_TEXT = (
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "spend cap",
    "spending cap",
    "insufficient_quota",
    "billing",
    "authentication",
    "unauthorized",
    "forbidden",
    "permission_denied",
    "model not found",
    "not found",
)


def _provider_failure_reason(exc: Exception) -> str:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            text = f"{text} {response.text[:500]}"
        except Exception:
            pass
    lowered = text.lower()
    if status_code in _PROVIDER_FATAL_STATUSES or any(marker in lowered for marker in _PROVIDER_FATAL_TEXT):
        status = f"HTTP {status_code}" if status_code else type(exc).__name__
        return f"ingestion_llm_provider_unhealthy:{status}:{type(exc).__name__}"
    return f"ingestion_llm_error:{type(exc).__name__}"

_YT_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "yt":    "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
_YT_CHANNEL_RSS  = "https://www.youtube.com/feeds/videos.xml?channel_id={id}"
_YT_PLAYLIST_RSS = "https://www.youtube.com/feeds/videos.xml?playlist_id={id}"
_MAX_SOURCE_TEXT_CHARS = 12000
_EXTRACTION_CHUNK_CHARS = 12000
_DEFAULT_FINDINGS_PER_PASS = 8
_MAX_EXTRACTION_PASSES_PER_CHUNK = 25  # runaway guard, not a normal finding target
_MAX_TITLE_WORDS = 12
_FOCUS_STOPWORDS = {
    "about", "after", "agent", "agents", "and", "are", "autonomous", "before",
    "from", "general", "interview", "into", "lessons", "research", "that",
    "the", "their", "this", "with", "your",
}
_DURABLE_SIGNAL_TERMS = {
    # Technical / systems research signals
    "architecture", "benchmark", "boundary", "capability", "constraint",
    "deploy", "deployment", "determinism", "enables", "evidence", "framework",
    "governance", "guardrail", "guardrails", "latency", "model", "monitoring",
    "monetizing", "need", "needs", "orchestration", "pattern", "performance",
    "pipeline", "provenance", "quality", "requires", "risk", "routing",
    "sandboxing", "security", "should", "system", "throughput", "tracking",
    "tradeoff", "workflow", "workflows",
    # Business / brand / creative strategy signals
    "audience", "brand", "brands", "branding", "campaign", "codes", "community", "content",
    "conversion", "creative", "customer", "customers", "demand", "differentiation",
    "distribution", "experience", "funnel", "identity", "ladder", "launch", "market", "narrative",
    "positioning", "premium", "pricing", "product", "products", "scarce", "scarcity", "status",
    "strategy", "tactic", "tactics", "value", "visual",
}
_DOMAIN_TERMS = {
    # AI/software domains
    "agent", "agentic", "agents", "ai", "autonomy", "autonomous", "code",
    "coding", "llm", "llms", "model", "models", "multi-agent", "software",
    # General business/creative domains; prevents non-AI research from being
    # over-filtered as if every durable claim had to be about agent systems.
    "audience", "brand", "brands", "branding", "business", "content", "creative",
    "customer", "customers", "market", "positioning", "premium", "product", "strategy",
}
_LOW_VALUE_PATTERNS = (
    r"\b(ad read|sponsor|sponsored|new sponsor|promo code|discount code)\b",
    r"\b(like and subscribe|subscribe to|hit the bell)\b",
    r"\b(mail\s*trap|transactional and promotional email)\b",
    r"\b(by the way|why is everyone|going to be proud of me|making that joke)\b",
    r"\b(i don't have a psychosis|i thought you were|i think i do one pun)\b",
)


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
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\]?", " ", text)
    text = re.sub(r"(?m)^\s*(speaker\s*\d+|host|guest|interviewer|interviewee)\s*:\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b(transcript|auto-generated transcript|foreign|music|applause)\b", " ", text)
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


def build_recall_cues(*parts: str, max_cues: int = 6) -> list[str]:
    """Build compact internal phrases for later associative recall."""

    text = _clean_source_text(" ".join(part for part in parts if part))
    terms = [
        term
        for term in re.findall(r"[a-z][a-z0-9+-]{2,}", text.lower())
        if term not in _FOCUS_STOPWORDS and term not in {"source", "finding", "knowledge"}
    ]
    cues: list[str] = []
    seen: set[str] = set()
    words = text.split()
    if words:
        first = " ".join(words[: min(8, len(words))]).strip(" ,.;:")
        if first:
            cues.append(first.lower())
            seen.add(first.lower())
    for size in (3, 2):
        for i in range(0, max(0, len(terms) - size + 1)):
            cue = " ".join(terms[i:i + size])
            if cue in seen:
                continue
            cues.append(cue)
            seen.add(cue)
            if len(cues) >= max_cues:
                return cues
    return cues[:max_cues]


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


def _source_text_chunks(text: str, *, max_chars: int = _EXTRACTION_CHUNK_CHARS) -> list[str]:
    cleaned = _clean_source_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in cleaned.split():
        addition = len(word) + (1 if current else 0)
        if current and current_len + addition > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += addition
    if current:
        chunks.append(" ".join(current))
    return chunks


def _finding_limit(max_findings: int | None) -> int | None:
    if max_findings is None:
        return None
    try:
        value = int(max_findings)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _dedupe_findings(findings: list[dict[str, Any]], *, max_findings: int | None = None) -> list[dict[str, Any]]:
    limit = _finding_limit(max_findings)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        key = _normalize_titleish(" ".join([
            str(finding.get("title") or ""),
            str(finding.get("summary") or ""),
            str(finding.get("content") or "")[:180],
        ]))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
        if limit is not None and len(deduped) >= limit:
            break
    return deduped


def _chunk_metadata(findings: list[dict[str, Any]], *, chunk_index: int, chunks_total: int) -> list[dict[str, Any]]:
    for finding in findings:
        finding["chunk_index"] = chunk_index
        finding["chunks_total"] = chunks_total
    return findings


def _focus_terms(agent_focus: str) -> set[str]:
    terms = {
        term
        for term in re.findall(r"[a-z][a-z0-9+-]{2,}", (agent_focus or "").lower())
        if term not in _FOCUS_STOPWORDS
    }
    return terms | _DOMAIN_TERMS


def _is_durable_finding(*, title: str, summary: str, content: str, agent_focus: str = "") -> bool:
    text = _clean_source_text(" ".join([title, summary, content]))
    words = text.split()
    if len(words) < 8:
        return False

    lowered = text.lower()
    if any(re.search(pattern, lowered) for pattern in _LOW_VALUE_PATTERNS):
        return False

    # Reject transcript banter and host chatter unless it contains an actual
    # reusable claim. This catches jokes, asides, and conversational fragments.
    first_personish = re.search(r"\b(i|i'm|i've|we|we're|you|your)\b", lowered)
    terms = set(re.findall(r"[a-z][a-z0-9+-]{2,}", lowered))
    has_signal = bool(terms & _DURABLE_SIGNAL_TERMS)
    if first_personish and not has_signal:
        return False

    focus = _focus_terms(agent_focus)
    if agent_focus and not (terms & focus):
        return False
    if not has_signal and len(terms & _DOMAIN_TERMS) < 2:
        return False

    # Pure questions are spark material, not findings extracted from sources.
    if text.endswith("?") and not has_signal:
        return False

    return True


def _fallback_findings(
    *,
    source_title: str,
    source_text: str,
    max_findings: int | None,
    confidence: float,
    agent_focus: str = "",
) -> list[dict[str, Any]]:
    limit = _finding_limit(max_findings)
    units = _sentence_units(source_text)
    findings: list[dict[str, Any]] = []
    for unit in units:
        index = len(findings) + 1
        title = _title_from_content(unit, source_title, index)
        summary = _summarize_text(unit)
        if not _is_durable_finding(title=title, summary=summary, content=unit, agent_focus=agent_focus):
            continue
        findings.append({
            "title": title,
            "summary": summary,
            "content": unit,
            "confidence": confidence,
            "recall_cues": build_recall_cues(title, summary, unit),
        })
        if limit is not None and len(findings) >= limit:
            break
    return findings


def _validated_findings(
    raw_findings: Any,
    *,
    source_title: str,
    fallback_text: str,
    max_findings: int | None,
    confidence: float,
    agent_focus: str = "",
) -> list[dict[str, Any]]:
    limit = _finding_limit(max_findings)
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
        if not _is_durable_finding(title=title, summary=summary, content=content, agent_focus=agent_focus):
            continue
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
            "recall_cues": _normalize_recall_cues(raw.get("recall_cues"), title, summary, content),
        })
        if limit is not None and len(findings) >= limit:
            break
    if findings:
        return findings
    return _fallback_findings(
        source_title=source_title,
        source_text=fallback_text,
        max_findings=max_findings,
        confidence=confidence,
        agent_focus=agent_focus,
    )


def _normalize_recall_cues(raw: Any, title: str, summary: str, content: str) -> list[str]:
    cues: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            cue = _clean_source_text(str(item or "")).lower()
            if cue and cue not in cues:
                cues.append(cue[:120])
    if cues:
        return cues[:6]
    return build_recall_cues(title, summary, content)


def _dedupe_prefiltered_text(text: str) -> str:
    """Remove exact repeated bullets/sentences from cheap prefilter output."""

    cleaned = _clean_source_text(text)
    if not cleaned:
        return ""
    rough_units = re.split(r"\s*(?:[•*]\s+|(?<=[.!?])\s+)\s*", cleaned)
    units: list[str] = []
    seen: set[str] = set()
    for unit in rough_units:
        unit = unit.strip(" -–—;:,\t\n")
        if len(unit.split()) < 5:
            continue
        key = _normalize_titleish(unit[:240])
        if not key or key in seen:
            continue
        seen.add(key)
        units.append(unit)
    return "\n".join(f"- {unit}" for unit in units) if units else cleaned


async def prefilter_source_text(
    *,
    source_title: str,
    source_text: str,
    source_type: str,
    source_url: str = "",
    agent_focus: str = "",
    llm: Any,
    min_chars_to_filter: int = 4000,
) -> tuple[str, dict[str, Any]]:
    """Use a cheap LLM pass to remove low-value transcript/source filler.

    The prefilter is deliberately conservative: it may delete ads, intros, jokes,
    repeated stories, vague motivation, and meta-conversation, but it must keep
    source-grounded ideas, theories, answers, frameworks, mechanisms, examples,
    tradeoffs, and sharp questions. If the filtered text looks suspiciously short
    or the model errors, the original cleaned text is returned.
    """

    cleaned = _clean_source_text(source_text)
    metadata: dict[str, Any] = {
        "prefilter_enabled": True,
        "prefilter_applied": False,
        "prefilter_model": getattr(llm, "model", None),
        "prefilter_original_chars": len(cleaned),
    }
    if len(cleaned) < min_chars_to_filter:
        metadata.update({
            "prefilter_skip_reason": "source_below_min_chars",
            "prefilter_filtered_chars": len(cleaned),
            "prefilter_reduction_ratio": 0.0,
        })
        return cleaned, metadata

    chunks = _source_text_chunks(cleaned)
    filtered_chunks: list[str] = []
    try:
        for chunk_index, chunk in enumerate(chunks, start=1):
            prompt = f"""You are a conservative transcript/source prefilter for a semantic memory pipeline.

Source title: {source_title}
Source URL: {source_url or "unknown"}
Source type: {source_type}
Agent research focus: {agent_focus or "general durable knowledge"}
Chunk: {chunk_index} of {len(chunks)}

Task: remove bullshit before a stronger model extracts durable knowledge.

KEEP ONLY source-grounded material that contains at least one of:
- an idea, theory, answer, claim, mechanism, framework, mental model, decision rule, tradeoff, anti-pattern, useful example, definition, or open question
- a concrete practitioner lesson or causal explanation
- a specific disagreement, caveat, exception, or uncertainty worth remembering

DELETE:
- ads/sponsors/promo codes, intros/outros, calls to subscribe, greetings, pleasantries
- jokes, banter, social filler, tangents, repeated stories, recap loops
- vague motivation, generic inspiration, applause/music/transcript artifacts
- meta-conversation about the episode unless it contains a transferable lesson

Rules:
- Do NOT summarize across missing context.
- Do NOT invent or improve the source's claims.
- Preserve original meaning and useful specificity.
- Prefer compact cleaned paragraphs or bullets.
- If the whole chunk is junk, return exactly: NO_SIGNAL

Chunk text:
{chunk}"""
            raw = await llm.call(prompt, max_tokens=2200)
            filtered = _clean_source_text(raw)
            if not filtered or filtered.strip().upper() == "NO_SIGNAL":
                continue
            filtered_chunks.append(filtered)
    except Exception as exc:
        metadata.update({
            "prefilter_error": f"{type(exc).__name__}:{str(exc)[:120]}",
            "prefilter_filtered_chars": len(cleaned),
            "prefilter_reduction_ratio": 0.0,
        })
        logger.debug("Source prefilter failed for %r, using original text: %s", source_title, exc)
        return cleaned, metadata

    filtered_text = _dedupe_prefiltered_text("\n\n".join(filtered_chunks))
    # Guard against an overzealous cheap model nuking useful signal. A good
    # transcript filter can be highly compressive, so only reject near-empty
    # output rather than demanding a large retained percentage.
    if len(filtered_text) < max(160, int(len(cleaned) * 0.005)):
        metadata.update({
            "prefilter_error": "filtered_text_too_short_using_original",
            "prefilter_filtered_chars": len(filtered_text),
            "prefilter_reduction_ratio": 0.0,
        })
        return cleaned, metadata

    reduction = 1.0 - (len(filtered_text) / max(len(cleaned), 1))
    metadata.update({
        "prefilter_applied": True,
        "prefilter_filtered_chars": len(filtered_text),
        "prefilter_reduction_ratio": round(max(0.0, min(1.0, reduction)), 4),
        "prefilter_chunks_total": len(chunks),
        "prefilter_chunks_kept": len(filtered_chunks),
    })
    return filtered_text, metadata


async def extract_knowledge_findings(
    *,
    source_title: str,
    source_text: str,
    source_type: str,
    source_url: str = "",
    agent_focus: str = "",
    llm: Any | None = None,
    max_findings: int | None = None,
    confidence: float = 0.55,
    allow_heuristic_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Extract durable knowledge findings from any source text.

    Source title and URL are provenance. Returned finding titles describe the
    knowledge itself and intentionally avoid mirroring the article/video title.

    Long sources are chunked so extraction sees the whole source. ``max_findings``
    is an optional total ceiling; ``None`` or ``<= 0`` means no artificial
    source-level finding cap. Each chunk is processed in continuation passes
    until the extractor returns no new durable findings, with a high runaway
    guard to prevent broken model loops.
    """
    cleaned = _clean_source_text(source_text)
    if not cleaned:
        return []

    limit = _finding_limit(max_findings)
    chunks = _source_text_chunks(cleaned)
    chunks_total = len(chunks)
    all_findings: list[dict[str, Any]] = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        remaining = None if limit is None else limit - len(all_findings)
        if remaining is not None and remaining <= 0:
            break
        chunk_limit = _DEFAULT_FINDINGS_PER_PASS if remaining is None else min(remaining, _DEFAULT_FINDINGS_PER_PASS)

        chunk_findings: list[dict[str, Any]] = []
        guard_hit = False
        llm_attempted = llm is not None
        fallback_reason: str | None = None
        if llm is not None:
            try:
                for pass_index in range(1, _MAX_EXTRACTION_PASSES_PER_CHUNK + 1):
                    remaining = None if limit is None else limit - len(all_findings) - len(chunk_findings)
                    if remaining is not None and remaining <= 0:
                        break
                    pass_limit = _DEFAULT_FINDINGS_PER_PASS if remaining is None else min(remaining, _DEFAULT_FINDINGS_PER_PASS)
                    already_extracted = [
                        f"{finding.get('title', '')}: {str(finding.get('summary') or finding.get('content') or '')[:180]}"
                        for finding in chunk_findings
                    ][-20:]
                    already_text = "\n".join(f"- {claim}" for claim in already_extracted if claim.strip(": ")) or "None yet"
                    continuation_rule = (
                        "Return the next batch of durable findings not already listed. "
                        "If no additional durable findings remain in this chunk, return []."
                    )
                    prompt = f"""Extract durable knowledge findings from this {source_type} source chunk.

Source title: {source_title}
Source URL: {source_url or "unknown"}
Agent research focus: {agent_focus or "general durable knowledge"}
Chunk: {chunk_index} of {chunks_total}
Extraction pass: {pass_index}
Already extracted from this chunk:
{already_text}

Rules:
- Return distinct learnings, not a summary of the source.
- Each finding title must describe the knowledge claim itself.
- Do not use the source title as a finding title.
- Prefer specific, reusable claims over episode/article framing.
- Capture useful information, lessons, guidance, opinions, frameworks, anti-patterns, mental models, examples, and sharp questions when they are durable and reusable.
- Reject jokes, banter, asides, ad reads, sponsor mentions, intros/outros, repeated stories, vague motivation, generic platitudes, and meta-conversation.
- Reject product marketing unless it directly supports the research focus.
- {continuation_rule}
- Return up to {pass_limit} useful distinct findings in this pass.

Respond only as JSON:
[
  {{
    "title": "short claim title, max 80 characters",
    "summary": "one sentence summary",
    "content": "2-4 sentences preserving the useful knowledge",
    "recall_cues": ["2-6 short phrases describing when this should be remembered"],
    "confidence": 0.0
  }}
]

Source chunk text:
{chunk}"""
                    raw = await llm.call(prompt, max_tokens=max(1200, pass_limit * 350))
                    start, end = raw.find("["), raw.rfind("]") + 1
                    pass_findings: list[dict[str, Any]] = []
                    if start >= 0 and end > start:
                        parsed = json.loads(raw[start:end])
                        pass_findings = _validated_findings(
                            parsed,
                            source_title=source_title,
                            fallback_text=chunk,
                            max_findings=pass_limit,
                            confidence=confidence,
                            agent_focus=agent_focus,
                        )
                    if not pass_findings:
                        break
                    before = len(chunk_findings)
                    chunk_findings = _dedupe_findings(chunk_findings + pass_findings)
                    if len(chunk_findings) == before:
                        break
                    if pass_index >= _MAX_EXTRACTION_PASSES_PER_CHUNK and limit is None:
                        guard_hit = True
                        break
                    if limit is not None and len(all_findings) + len(chunk_findings) >= limit:
                        break
            except Exception as exc:
                fallback_reason = _provider_failure_reason(exc)
                if not allow_heuristic_fallback:
                    raise IngestionProviderError(fallback_reason, retryable=True) from exc
                logger.debug("LLM source finding extraction failed for chunk %s/%s, using fallback: %s", chunk_index, chunks_total, exc)

        if not chunk_findings:
            if llm_attempted and fallback_reason is None:
                fallback_reason = "llm_returned_no_valid_findings"
            if llm_attempted and not allow_heuristic_fallback:
                raise IngestionProviderError(fallback_reason or "ingestion_llm_returned_no_valid_findings")
            chunk_findings = _fallback_findings(
                source_title=source_title,
                source_text=chunk,
                max_findings=chunk_limit,
                confidence=confidence,
                agent_focus=agent_focus,
            )
            if llm_attempted:
                for finding in chunk_findings:
                    finding["llm_fallback_used"] = True
                    finding["llm_fallback_reason"] = fallback_reason
        if guard_hit:
            logger.warning(
                "source extraction continuation guard hit: source_title=%r source_type=%s source_url=%r chunk=%s/%s passes=%s findings_in_chunk=%s",
                source_title,
                source_type,
                source_url,
                chunk_index,
                chunks_total,
                _MAX_EXTRACTION_PASSES_PER_CHUNK,
                len(chunk_findings),
            )
            for finding in chunk_findings:
                finding["extraction_guard_hit"] = True
                finding["extraction_passes_completed"] = _MAX_EXTRACTION_PASSES_PER_CHUNK

        all_findings.extend(_chunk_metadata(chunk_findings, chunk_index=chunk_index, chunks_total=chunks_total))
        all_findings = _dedupe_findings(all_findings, max_findings=limit)

    return _dedupe_findings(all_findings, max_findings=limit)


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

    def __init__(
        self,
        api: Any,
        llm: Any | None = None,
        yt_search: Any | None = None,
        *,
        research_llm: Any | None = None,
        ingestion_llm: Any | None = None,
    ) -> None:
        self.api = api
        self.research_llm = research_llm or llm  # optional: query/source planning LLM
        self.ingestion_llm = ingestion_llm or llm  # optional: source extraction LLM
        self.yt_search = yt_search  # optional: YouTubeSearchClient or EchoSearchAsYouTube

    def _require_ingestion_llm(self) -> Any:
        if self.ingestion_llm is None:
            raise IngestionProviderError("ingestion_llm_provider_missing_or_unconfigured")
        return self.ingestion_llm

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
        queries = await _generate_search_queries(query_specialty, domains, llm=self.research_llm, n=4)
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
            from neo.core.youtube import get_fetcher
            fetcher = get_fetcher()
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: fetcher.fetch(video_id))
            full_text = data["text"]
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
            llm=self._require_ingestion_llm(),
            max_findings=None,
            confidence=confidence,
            allow_heuristic_fallback=False,
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
                    "chunk_index": finding.get("chunk_index"),
                    "chunks_total": finding.get("chunks_total"),
                    "extraction_guard_hit": finding.get("extraction_guard_hit"),
                    "extraction_passes_completed": finding.get("extraction_passes_completed"),
                    "llm_fallback_used": finding.get("llm_fallback_used", False),
                    "llm_fallback_reason": finding.get("llm_fallback_reason"),
                    "recall_cues": finding.get("recall_cues") or build_recall_cues(
                        finding["title"], finding.get("summary", ""), finding.get("content", "")
                    ),
                },
                generate_sparks=True,
                deduplicate=True,
            )
            if (result.get("metadata") or {}).get("llm_fallback_used"):
                node_id = result.get("id")
                if node_id:
                    await self.api.store.delete_node(node_id)
                raise IngestionProviderError("heuristic_fallback_node_rolled_back")
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
            llm=self._require_ingestion_llm(),
            max_findings=3,
            confidence=0.5,
            allow_heuristic_fallback=False,
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
                    "llm_fallback_used": finding.get("llm_fallback_used", False),
                    "llm_fallback_reason": finding.get("llm_fallback_reason"),
                    "recall_cues": finding.get("recall_cues") or build_recall_cues(
                        finding["title"], finding.get("summary", ""), finding.get("content", "")
                    ),
                },
                generate_sparks=True,
                deduplicate=True,
            )
            if (result.get("metadata") or {}).get("llm_fallback_used"):
                node_id = result.get("id")
                if node_id:
                    await self.api.store.delete_node(node_id)
                raise IngestionProviderError("heuristic_fallback_node_rolled_back")
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
