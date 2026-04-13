"""Web search client for background spark resolution."""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WebSearchClient:
    """Thin wrapper over Tavily or Exa for background research.

    Both providers are queried with recency bias: Tavily uses a 90-day
    rolling window and advanced search depth; Exa uses autoprompt + recency
    sorting. Results include publish date where available.
    """

    def __init__(self, provider: str, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5, days: int = 90) -> list[dict[str, Any]]:
        """Search and return results sorted by relevance × recency.

        days: prefer content published within this many days (0 = no filter).
        """
        if self.provider == "tavily":
            return await self._tavily(query, max_results, days)
        elif self.provider == "exa":
            return await self._exa(query, max_results, days)
        else:
            logger.warning("Unknown search provider: %s", self.provider)
            return []

    async def multi_search(
        self,
        queries: list[str],
        max_results_per_query: int = 3,
        days: int = 90,
    ) -> list[dict[str, Any]]:
        """Run multiple queries and merge results, deduplicating by URL."""
        import asyncio
        all_results: list[dict] = []
        seen_urls: set[str] = set()
        tasks = [self.search(q, max_results=max_results_per_query, days=days) for q in queries]
        groups = await asyncio.gather(*tasks, return_exceptions=True)
        for group in groups:
            if isinstance(group, Exception):
                logger.warning("multi_search sub-query failed: %s", group)
                continue
            for r in group:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
        return all_results

    async def _tavily(self, query: str, max_results: int, days: int) -> list[dict]:
        import httpx
        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",    # richer snippets than "basic"
            "include_answer": False,
            "include_raw_content": False,
        }
        if days > 0:
            payload["days"] = days         # Tavily recency filter

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("results", []):
                results.append({
                    "title":       r.get("title", ""),
                    "url":         r.get("url", ""),
                    "snippet":     r.get("content", ""),
                    "published":   r.get("published_date", ""),
                    "score":       r.get("score", 0.0),
                })
            # Sort by Tavily's own relevance score descending
            results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return results

    async def _exa(self, query: str, max_results: int, days: int) -> list[dict]:
        import httpx
        from datetime import datetime, timedelta, timezone
        payload: dict[str, Any] = {
            "query":        query,
            "numResults":   max_results,
            "useAutoprompt": True,
            "type":         "neural",
            "contents":     {"text": {"maxCharacters": 500}},
        }
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            payload["startPublishedDate"] = cutoff

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("results", []):
                text_content = ""
                if isinstance(r.get("contents"), dict):
                    text_content = r["contents"].get("text", "")
                results.append({
                    "title":     r.get("title", ""),
                    "url":       r.get("url", ""),
                    "snippet":   text_content or r.get("text", ""),
                    "published": r.get("publishedDate", ""),
                    "score":     r.get("score", 0.0),
                })
            return results


class NullWebSearch:
    """No-op when no search API is configured."""
    async def search(self, query: str, max_results: int = 5, days: int = 90) -> list[dict]:
        return []

    async def multi_search(self, queries: list[str], **kwargs) -> list[dict]:
        return []
