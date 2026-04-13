"""Spark resolver — role-isolated debate and blind judgment with research.

Research strategy:
  1. Pull internal graph context around the spark.
  2. Generate 2-3 search queries from different angles (not just the raw
     spark description) so we explore the topic broadly.
  3. Prefer recent content — Tavily/Exa use a 90-day window by default.
  4. For any YouTube URLs in results, fetch the actual transcript excerpt.
  5. Run role-isolated candidate agents: A, B, and AB synthesis.
  6. Run blind judge agents and apply the winning decision.
  7. Enable spark generation on any new finding so the research chain
     continues — findings naturally raise follow-up questions.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_ACTIONS = {"create_node", "update_target", "resolve_no_change", "abandon"}
_CANDIDATE_LABELS = ("A", "B", "AB")


def _extract_json(raw: str, fallback: Any) -> Any:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    obj_start, obj_end = raw.find("{"), raw.rfind("}") + 1
    arr_start, arr_end = raw.find("["), raw.rfind("]") + 1
    try:
        if obj_start >= 0 and obj_end > obj_start and (arr_start < 0 or obj_start < arr_start):
            return json.loads(raw[obj_start:obj_end])
        if arr_start >= 0 and arr_end > arr_start:
            return json.loads(raw[arr_start:arr_end])
    except Exception:
        return fallback
    return fallback


def _words(text: str, limit: int) -> str:
    parts = (text or "").split()
    if len(parts) <= limit:
        return " ".join(parts)
    return " ".join(parts[:limit]) + "..."


def _strategy_for(spark_type: str) -> dict[str, str]:
    strategies = {
        "contradiction": {
            "objective": "Resolve a possible contradiction without overclaiming.",
            "a": "Defend one plausible reading or side of the tension.",
            "b": "Defend the competing reading or side of the tension.",
            "ab": "Reconcile, choose, or preserve uncertainty with a clear graph action.",
        },
        "open_question": {
            "objective": "Answer the open question if durable knowledge is available.",
            "a": "Develop one evidence-backed answer to the question.",
            "b": "Develop a distinct evidence-backed answer, mechanism, or skeptical framing.",
            "ab": "Synthesize the best current answer and remaining uncertainty.",
        },
        "weak_edge": {
            "objective": "Decide whether a graph relationship should be kept, revised, or discarded.",
            "a": "Argue the relationship is meaningful and useful.",
            "b": "Argue the relationship is weak, indirect, misleading, or mis-typed.",
            "ab": "Decide the most accurate graph treatment.",
        },
        "isolated_node": {
            "objective": "Integrate isolated knowledge when a durable connection exists.",
            "a": "Argue the strongest placement or relationship for this knowledge.",
            "b": "Argue an alternative placement, relationship, or reason to leave it isolated.",
            "ab": "Decide whether to store, link, update, or close without change.",
        },
        "thin_domain": {
            "objective": "Identify whether the domain gap has a durable next knowledge target.",
            "a": "Argue the highest-value missing knowledge to add next.",
            "b": "Argue an alternative gap or why the spark is not worth resolving now.",
            "ab": "Decide the durable knowledge action or close without graph change.",
        },
    }
    return strategies.get(spark_type, strategies["open_question"])


def _candidate_from_raw(label: str, raw: Any, fallback_text: str) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    content = str(data.get("content") or data.get("rationale") or fallback_text or "").strip()
    title = str(data.get("title") or _words(content, 10) or f"Spark resolution {label}").strip()
    summary = str(data.get("summary") or _words(content, 28) or title).strip()
    action = str(data.get("recommended_action") or data.get("action") or "create_node").strip()
    if action not in _ACTIONS:
        action = "create_node"
    node_type = str(data.get("node_type") or "finding").strip()
    if node_type not in {"finding", "theory", "synthesis"}:
        node_type = "finding"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.6))))
    except (TypeError, ValueError):
        confidence = 0.6
    return {
        "label": label,
        "title": title[:90],
        "summary": summary[:240],
        "content": content,
        "confidence": confidence,
        "recommended_action": action,
        "node_type": node_type,
        "rationale": str(data.get("rationale") or summary).strip()[:500],
    }


def _vote_from_raw(raw: Any, judge_index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ranking = raw.get("ranking")
    if not isinstance(ranking, list):
        winner = str(raw.get("winner") or "").strip().upper()
        ranking = [winner] if winner in _CANDIDATE_LABELS else []
    clean_ranking: list[str] = []
    for label in ranking:
        label = str(label).strip().upper()
        if label in _CANDIDATE_LABELS and label not in clean_ranking:
            clean_ranking.append(label)
    for label in _CANDIDATE_LABELS:
        if label not in clean_ranking:
            clean_ranking.append(label)
    winner = clean_ranking[0]
    return {
        "judge": judge_index,
        "ranking": clean_ranking,
        "winner": winner,
        "rationale": str(raw.get("rationale") or "").strip()[:500],
    }


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

    # ── Candidate / judge agents ─────────────────────────────────────────────

    async def _candidate_agent(
        self,
        *,
        label: str,
        role: str,
        base: str,
        strategy: dict[str, str],
        prior_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prior = ""
        if prior_candidates:
            prior = "\n\nExisting candidates:\n" + "\n\n".join(
                f"Candidate {c['label']}: {c['title']}\nSummary: {c['summary']}\nAction: {c['recommended_action']}\nContent: {c['content']}"
                for c in prior_candidates
            )
        prompt = f"""ROLE: POSITION_{label}

You are one role-isolated spark resolution agent. You do not share hidden state
with the other agents. Use only the supplied graph context and evidence.

Resolution objective: {strategy['objective']}
Your assignment: {role}

{base}{prior}

Return JSON only:
{{
  "title": "short durable knowledge title",
  "summary": "one sentence",
  "content": "2-4 sentences with the resolved insight or rationale",
  "confidence": 0.0,
  "recommended_action": "create_node|update_target|resolve_no_change|abandon",
  "node_type": "finding|theory|synthesis",
  "rationale": "why this action fits the spark"
}}"""
        raw = await self.llm.call(prompt, max_tokens=900)
        parsed = _extract_json(raw, {})
        return _candidate_from_raw(label, parsed, raw)

    async def _judge_candidates(
        self,
        *,
        base: str,
        candidates: list[dict[str, Any]],
        strategy: dict[str, str],
        judge_count: int = 3,
    ) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
        candidate_text = "\n\n".join(
            f"Candidate {c['label']}\nTitle: {c['title']}\nSummary: {c['summary']}\nAction: {c['recommended_action']}\nContent: {c['content']}"
            for c in candidates
        )
        votes: list[dict[str, Any]] = []
        scores = {label: 0 for label in _CANDIDATE_LABELS}
        for idx in range(1, judge_count + 1):
            prompt = f"""ROLE: JUDGE_{idx}

You are an independent blind judge for a spark resolution tournament.
Judge the candidates only by how well they resolve the spark.

Resolution objective: {strategy['objective']}

Criteria:
- resolves the spark directly
- grounded in graph context and evidence
- durable enough to store as knowledge, or correctly chooses no graph change
- avoids overclaiming
- preserves uncertainty when needed

{base}

Candidates:
{candidate_text}

Return JSON only:
{{
  "ranking": ["AB", "A", "B"],
  "winner": "AB",
  "rationale": "brief reason for the ranking"
}}"""
            try:
                raw = await self.llm.call(prompt, max_tokens=500)
                vote = _vote_from_raw(_extract_json(raw, {}), idx)
            except Exception as exc:
                logger.debug("Resolver judge %d failed: %s", idx, exc)
                vote = None
            if vote is None:
                continue
            votes.append(vote)
            for points, label in zip((3, 2, 1), vote["ranking"], strict=False):
                scores[label] = scores.get(label, 0) + points
        if not votes:
            scores["AB"] = 1
            return "AB", [], scores
        winner = max(_CANDIDATE_LABELS, key=lambda label: (scores.get(label, 0), label == "AB"))
        return winner, votes, scores

    async def _apply_decision(
        self,
        *,
        spark: dict,
        agent: dict,
        candidate: dict[str, Any],
        decision_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        spark_id = spark["id"]
        agent_id = agent["id"]
        action = candidate["recommended_action"]
        notes = candidate.get("summary") or candidate.get("rationale") or candidate["title"]

        if action == "abandon":
            result = await self.api.store.abandon_spark(
                spark_id,
                reason=notes,
                metadata=decision_metadata,
            )
            return {"success": True, "spark_id": spark_id, "outcome": "abandoned", "spark": result}

        if action == "resolve_no_change":
            result = await self.api.resolve_spark(
                spark_id=spark_id,
                node_ids=[],
                notes=notes,
                metadata=decision_metadata,
            )
            return {"success": True, "spark_id": spark_id, "outcome": "resolved_no_change", "spark": result}

        if action == "update_target" and spark.get("target_node_id"):
            node_id = spark["target_node_id"]
            node = await self.api.update_node(
                node_id=node_id,
                content=candidate["content"],
                summary=candidate["summary"],
                confidence=candidate["confidence"],
                metadata={"spark_resolution": decision_metadata},
            )
            result = await self.api.resolve_spark(
                spark_id=spark_id,
                node_ids=[node_id],
                notes=notes,
                metadata=decision_metadata,
            )
            return {
                "success": True,
                "spark_id": spark_id,
                "outcome": "updated_node",
                "node_id": node_id,
                "node": node,
                "spark": result,
            }

        node = await self.api.store_node(
            agent_id=agent_id,
            node_type=candidate["node_type"],
            title=candidate["title"],
            content=candidate["content"],
            summary=candidate["summary"],
            confidence=candidate["confidence"],
            parent_id=spark.get("target_node_id"),
            spark_id=spark_id,
            domain=spark.get("node_domain") or spark.get("domain") or ((agent.get("domains") or [None])[0]),
            metadata={"spark_resolution": decision_metadata},
            generate_sparks=True,
            deduplicate=True,
        )
        result = await self.api.resolve_spark(
            spark_id=spark_id,
            node_ids=[node["id"]],
            notes=notes,
            metadata=decision_metadata,
        )
        return {
            "success": True,
            "spark_id": spark_id,
            "outcome": "created_node" if not node.get("duplicate") else "reused_duplicate",
            "node_id": node["id"],
            "node": node,
            "spark": result,
        }

    # ── Main resolution flow ──────────────────────────────────────────────────

    async def resolve(
        self,
        spark: dict,
        agent: dict,
        *,
        mode: str = "apply",
        trigger: str = "background",
    ) -> dict[str, Any]:
        if mode not in {"preview", "apply"}:
            raise ValueError("mode must be 'preview' or 'apply'")
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
        strategy = _strategy_for(spark.get("spark_type", "open_question"))
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

        # 6. Role-isolated agents + blind judges
        try:
            candidate_a = await self._candidate_agent(
                label="A",
                role=strategy["a"],
                base=base,
                strategy=strategy,
            )
            candidate_b = await self._candidate_agent(
                label="B",
                role=strategy["b"],
                base=base,
                strategy=strategy,
                prior_candidates=[candidate_a],
            )
            candidate_ab = await self._candidate_agent(
                label="AB",
                role=strategy["ab"],
                base=base,
                strategy=strategy,
                prior_candidates=[candidate_a, candidate_b],
            )
            candidates = [candidate_a, candidate_b, candidate_ab]
            winner, judge_votes, judge_scores = await self._judge_candidates(
                base=base,
                candidates=candidates,
                strategy=strategy,
            )
        except Exception as e:
            logger.warning("Resolver: debate/judgment failed: %s", e)
            return {"success": False, "spark_id": spark_id, "error": str(e)}

        winning_candidate = next(c for c in candidates if c["label"] == winner)
        decision_metadata = {
            "resolution_method": "debate_judge_v1",
            "trigger": trigger,
            "mode": mode,
            "objective": strategy["objective"],
            "winner": winner,
            "winning_action": winning_candidate["recommended_action"],
            "queries": queries,
            "evidence": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "published": r.get("published", ""),
                    "score": r.get("score", 0.0),
                }
                for r in results[:8]
            ],
            "candidate_summaries": {
                c["label"]: {
                    "title": c["title"],
                    "summary": c["summary"],
                    "recommended_action": c["recommended_action"],
                    "confidence": c["confidence"],
                }
                for c in candidates
            },
            "judge_votes": judge_votes,
            "judge_scores": judge_scores,
        }

        if mode == "preview":
            return {
                "success": True,
                "spark_id": spark_id,
                "outcome": "preview",
                "winner": winner,
                "winning_candidate": winning_candidate,
                "candidates": candidates,
                "judge_votes": judge_votes,
                "judge_scores": judge_scores,
                "queries": queries,
                "evidence_count": len(results),
            }

        try:
            applied = await self._apply_decision(
                spark=spark,
                agent=agent,
                candidate=winning_candidate,
                decision_metadata=decision_metadata,
            )
            logger.info(
                "Resolver: spark %s → %s via candidate %s",
                spark_id,
                applied.get("outcome"),
                winner,
            )
            return {
                **applied,
                "winner": winner,
                "winning_candidate": {
                    "title": winning_candidate["title"],
                    "summary": winning_candidate["summary"],
                    "recommended_action": winning_candidate["recommended_action"],
                },
                "judge_scores": judge_scores,
            }
        except Exception as e:
            logger.warning("Resolver: apply failed: %s", e)
            return {"success": False, "spark_id": spark_id, "error": str(e)}
