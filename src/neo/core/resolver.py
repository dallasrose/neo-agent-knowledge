"""Background spark resolver — intelligent debate pattern with web research.

Research strategy:
  1. Pull internal graph context around the spark.
  2. Generate 2-3 search queries from different angles (not just the raw
     spark description) so we explore the topic broadly.
  3. Prefer recent content — Tavily/Exa use a 90-day window by default.
  4. For any YouTube URLs in results, fetch the actual transcript excerpt.
  5. Run a 3-call debate: argue for → argue against → synthesise.
  6. Store the settled conclusion as a finding node and resolve the spark.
  7. Enable spark generation on the new finding so the research chain
     continues — findings naturally raise follow-up questions.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResolutionLLM:
    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        self._model = model

    async def call(self, prompt: str, max_tokens: int = 1024) -> str:
        response = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=60,
        )
        # Skip ThinkingBlocks — grab the first block that actually has text
        text_block = next((b for b in response.content if hasattr(b, "text")), None)
        if text_block is None:
            raise ValueError("No text block in LLM response")
        return text_block.text.strip()


class SparkResolver:
    def __init__(self, api: Any, llm: ResolutionLLM, web_search: Any) -> None:
        self.api = api
        self.llm = llm
        self.web_search = web_search

    # ── Query generation ──────────────────────────────────────────────────────

    async def _generate_search_queries(
        self,
        description: str,
        target_title: str,
        agent_focus: str,
        n: int = 3,
    ) -> list[str]:
        """Use the LLM to generate n focused search queries for this spark.

        Rather than just searching the raw spark description, we generate
        angles: the core question, a practitioner perspective, and a
        domain-specific framing. This broadens the evidence base and
        surfaces more relevant recent content.
        """
        focus_line = f"Agent research direction: {agent_focus}\n" if agent_focus else ""
        prompt = f"""{focus_line}Generate {n} distinct web search queries to research this question:

Spark: {description}
Context node: {target_title}

Rules:
- Each query must be a concise search string (6-12 words), NOT a sentence.
- Cover different angles: the direct question, a real-world example, and a practitioner/expert perspective.
- Prefer queries that would surface recent articles, interviews, or podcast discussions.
- Tailor to the agent's research direction if provided.

Respond with a JSON array of strings only, e.g. ["query one", "query two", "query three"]"""
        try:
            raw = await self.llm.call(prompt, max_tokens=256)
            start, end = raw.find("["), raw.rfind("]") + 1
            queries = json.loads(raw[start:end])
            if isinstance(queries, list):
                valid = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
                if valid:
                    return valid[:n]
        except Exception as exc:
            logger.debug("Query generation failed, falling back: %s", exc)
        # Fallback: raw description + domain-scoped variant
        fallbacks = [description]
        if agent_focus:
            domain_words = agent_focus.split()[:4]
            fallbacks.append(f"{description} {' '.join(domain_words)}")
        return fallbacks

    # ── YouTube transcript enrichment ─────────────────────────────────────────

    async def _fetch_transcripts(self, results: list[dict], query: str) -> list[str]:
        """For any YouTube URLs in search results, fetch a relevant transcript excerpt."""
        excerpts: list[str] = []
        try:
            from neo.core.youtube import is_youtube_url, get_fetcher
            fetcher = get_fetcher()
            loop = asyncio.get_event_loop()
            for r in results:
                url = r.get("url") or r.get("link") or ""
                if url and is_youtube_url(url):
                    try:
                        data = await loop.run_in_executor(
                            None,
                            lambda u=url: fetcher.fetch_relevant_excerpt(u, query, max_chars=1000),
                        )
                        excerpt = data.get("excerpt", "")
                        if excerpt:
                            title = r.get("title", url)
                            excerpts.append(f"[Transcript: {title}]\n{excerpt}")
                            logger.info(
                                "Resolver: transcript fetched for %s (%d chars)",
                                url, len(excerpt),
                            )
                    except Exception as yt_exc:
                        logger.debug("Resolver: transcript skip %s: %s", url, yt_exc)
        except ImportError:
            pass
        return excerpts

    # ── Main resolution flow ──────────────────────────────────────────────────

    async def resolve(self, spark: dict, agent: dict) -> dict[str, Any]:
        agent_id = agent["id"]
        spark_id = spark["id"]
        description = spark.get("description", "")
        target_title = spark.get("target_title", "")
        target_content = (spark.get("target_content") or "")[:400]

        specialty = (agent.get("specialty") or "").strip()
        domains = agent.get("domains") or []
        domain_str = ", ".join(domains) if domains else ""
        agent_focus = specialty or domain_str

        logger.info("Resolver: spark %s — %s", spark_id, description[:80])

        # 1. Internal graph context
        try:
            ctx = await self.api.search_knowledge(
                agent_id=agent_id, query=description, top_k=5, hop_depth=1, token_budget=800
            )
            internal = "\n".join(
                f"- [{n.get('node_type')}] {n.get('title')}: {n.get('summary') or ''}"
                for n in ctx.get("nodes", [])
            )
        except Exception as e:
            logger.warning("Resolver: internal search failed: %s", e)
            internal = ""

        # 2. Generate intelligent search queries
        queries = await self._generate_search_queries(
            description, target_title, agent_focus, n=3
        )
        logger.info("Resolver: searching with %d queries: %s", len(queries), queries)

        # 3. Multi-query web search (deduped, recency-biased)
        try:
            results = await self.web_search.multi_search(
                queries, max_results_per_query=3, days=90
            )
            # Format results — include publish date when available for the LLM to reason about recency
            web_lines: list[str] = []
            for r in results[:8]:  # cap at 8 merged results
                date_tag = f" [{r['published']}]" if r.get("published") else ""
                web_lines.append(f"- {r['title']}{date_tag}: {r['snippet'][:300]}")
            web = "\n".join(web_lines)
        except Exception as e:
            logger.warning("Resolver: web search failed: %s", e)
            results = []
            web = ""

        # 4. YouTube transcript enrichment
        transcript_excerpts = await self._fetch_transcripts(results, description)

        # 5. Assemble debate context
        focus_line = f"Agent research direction: {agent_focus}\n" if agent_focus else ""
        transcripts_section = (
            "\n\nPodcast / video transcripts:\n" + "\n\n".join(transcript_excerpts)
            if transcript_excerpts else ""
        )
        base = f"""{focus_line}Spark: {description}
Context node: {target_title}
{target_content}

Internal knowledge:
{internal or 'None'}

Web research (recency-biased, dates shown where available):
{web or 'None'}{transcripts_section}"""

        # 6. Debate + synthesis
        try:
            pos_a = await self.llm.call(
                f"{base}\n\nArgue that this spark's question or tension is real and important. "
                "Ground your argument in the evidence above. 2-3 sentences."
            )
            pos_b = await self.llm.call(
                f"{base}\n\nPosition A: {pos_a}\n\n"
                "Argue the other side — complicate, refute, or add important nuance. "
                "Use specific evidence where possible. 2-3 sentences."
            )
            raw = await self.llm.call(
                f"""{base}

Position A: {pos_a}
Position B: {pos_b}

Synthesise into a settled conclusion grounded in the evidence. Prefer recent findings.
Respond as JSON only:
{{"title": "short noun phrase under 60 chars", "summary": "one compressed sentence", "content": "2-4 sentence settled conclusion citing evidence", "confidence": 0.0}}"""
            )
            start, end = raw.find("{"), raw.rfind("}") + 1
            result = json.loads(raw[start:end])
        except Exception as e:
            logger.warning("Resolver: debate/synthesis failed: %s", e)
            return {"success": False, "spark_id": spark_id, "error": str(e)}

        # 7. Store finding + resolve spark
        # generate_sparks=True so the finding can raise follow-up questions,
        # creating a research chain: finding → new sparks → future resolutions.
        try:
            node = await self.api.store_node(
                agent_id=agent_id,
                node_type="finding",
                title=result["title"],
                content=result["content"],
                summary=result.get("summary", ""),
                confidence=float(result.get("confidence", 0.6)),
                spark_id=spark_id,
                domain=spark.get("node_domain") or (domains[0] if domains else None),
                generate_sparks=True,   # follow the research path
            )
            await self.api.store.resolve_spark(
                spark_id=spark_id,
                resolved_node_ids=[node["id"]],
                notes=result.get("summary", ""),
            )
            logger.info(
                "Resolver: spark %s → finding node %s (sparks queued)",
                spark_id, node["id"],
            )
            return {"success": True, "spark_id": spark_id, "node_id": node["id"]}
        except Exception as e:
            logger.warning("Resolver: store failed: %s", e)
            return {"success": False, "spark_id": spark_id, "error": str(e)}
