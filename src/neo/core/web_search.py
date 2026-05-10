"""Web search client for background spark resolution."""
from __future__ import annotations
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)


class WebSearchClient:
    """Thin wrapper over Tavily or Exa for background research.

    Both providers are queried with recency bias: Tavily uses a 90-day
    rolling window and advanced search depth; Exa uses autoprompt + recency
    sorting. Results include publish date where available.
    """

    def __init__(self, provider: str, api_key: str | None = None) -> None:
        self.provider = provider.strip().lower()
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5, days: int = 90) -> list[dict[str, Any]]:
        """Search and return results sorted by relevance × recency.

        days: prefer content published within this many days (0 = no filter).
        """
        if self.provider == "tavily":
            results = await self._tavily(query, max_results, days)
            return results or await self._duckduckgo_fallback(query, max_results, "tavily returned no results")
        elif self.provider == "exa":
            results = await self._exa(query, max_results, days)
            return results or await self._duckduckgo_fallback(query, max_results, "exa returned no results")
        elif self.provider in {"duckduckgo", "ddg"}:
            return await self._duckduckgo(query, max_results)
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

    async def _duckduckgo_fallback(self, query: str, max_results: int, reason: str) -> list[dict]:
        logger.info("Falling back to DuckDuckGo search: %s", reason)
        try:
            return await self._duckduckgo(query, max_results)
        except Exception as exc:
            logger.warning("DuckDuckGo fallback search failed: %s", exc)
            return []

    async def _tavily(self, query: str, max_results: int, days: int) -> list[dict]:
        import httpx
        if not self.api_key:
            logger.warning("Tavily search requested without an API key")
            return []
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
        if not self.api_key:
            logger.warning("Exa search requested without an API key")
            return []
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

    async def _duckduckgo(self, query: str, max_results: int) -> list[dict]:
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "NeoResearchBot/1.0"},
            )
            resp.raise_for_status()
            html = resp.text

        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        pattern = re.compile(
            r'<a[^>]+class="result-link"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(html):
            url = _normalize_duckduckgo_url(match.group("href"))
            if not url or url in seen_urls:
                continue
            title = _strip_html(match.group("title"))
            seen_urls.add(url)
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": "",
                    "published": "",
                    "score": max(0.0, 1.0 - (len(results) * 0.05)),
                }
            )
            if len(results) >= max_results:
                break
        return results


def _strip_html(value: str) -> str:
    from html import unescape

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _normalize_duckduckgo_url(href: str) -> str:
    href = unquote(_strip_html(href))
    parsed = urlparse(href)
    if parsed.query:
        params = parse_qs(parsed.query)
        redirect = params.get("uddg")
        if redirect:
            return redirect[0]
    return href


class NullWebSearch:
    """No-op when no search API is configured."""
    async def search(self, query: str, max_results: int = 5, days: int = 90) -> list[dict]:
        return []

    async def multi_search(self, queries: list[str], **kwargs) -> list[dict]:
        return []
