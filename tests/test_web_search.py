from __future__ import annotations

import pytest

from neo.config import Settings
from neo.core.web_search import WebSearchClient, _normalize_duckduckgo_url


class _FakeResponse:
    text = """
    <html><body>
      <a rel="nofollow" class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpaper">Example Paper</a>
      <a rel="nofollow" class="result-link" href="https://example.org/direct">Direct Result</a>
    </body></html>
    """

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    last_request: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        type(self).last_request = {"url": url, **kwargs}
        return _FakeResponse()


class _FakeDuckDuckGoFallbackClient(_FakeAsyncClient):
    async def post(self, url: str, **kwargs):
        class EmptyResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"results": []}

        return EmptyResponse()


def test_duckduckgo_redirect_url_is_normalized() -> None:
    assert _normalize_duckduckgo_url("/l/?uddg=https%3A%2F%2Fexample.com%2Fx") == "https://example.com/x"


def test_duckduckgo_counts_as_configured_without_key() -> None:
    settings = Settings(_env_file=None, search_provider="duckduckgo", search_api_key=None)

    assert settings.search_configured() is True


@pytest.mark.asyncio
async def test_duckduckgo_search_uses_keyless_lite_endpoint(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    results = await WebSearchClient("duckduckgo").search("semantic memory", max_results=2)

    assert _FakeAsyncClient.last_request["url"] == "https://lite.duckduckgo.com/lite/"
    assert _FakeAsyncClient.last_request["params"] == {"q": "semantic memory"}
    assert results == [
        {
            "title": "Example Paper",
            "url": "https://example.com/paper",
            "snippet": "",
            "published": "",
            "score": 1.0,
        },
        {
            "title": "Direct Result",
            "url": "https://example.org/direct",
            "snippet": "",
            "published": "",
            "score": 0.95,
        },
    ]


@pytest.mark.asyncio
async def test_tavily_falls_back_to_duckduckgo_when_empty(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeDuckDuckGoFallbackClient)

    results = await WebSearchClient("tavily", api_key="key").search("semantic memory", max_results=2)

    assert results[0]["url"] == "https://example.com/paper"
    assert results[1]["url"] == "https://example.org/direct"
