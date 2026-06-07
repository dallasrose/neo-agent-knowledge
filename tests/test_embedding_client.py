import httpx
import pytest

from neo.embedding.client import EmbeddingClient
import neo.embedding.client as embedding_client_module


class _FakeAsyncClient:
    responses: list[httpx.Response] = []

    def __init__(self, *args, **kwargs):
        self.posts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        self.posts += 1
        if not self.responses:
            raise AssertionError("No fake Gemini embedding response queued")
        return self.responses.pop(0)


def _response(status_code: int, payload: dict | None = None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload or {},
        headers=headers or {},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"),
    )


@pytest.mark.asyncio
async def test_gemini_embedding_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(embedding_client_module.settings, "embedding_provider", "gemini")
    monkeypatch.setattr(embedding_client_module.settings, "embedding_fallback_enabled", True)
    monkeypatch.setattr(embedding_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(embedding_client_module.asyncio, "sleep", fake_sleep)
    _FakeAsyncClient.responses = [
        _response(429, {"error": "rate limited"}, {"retry-after": "0"}),
        _response(200, {"embedding": {"values": [0.1, 0.2, 0.3]}}),
    ]

    client = EmbeddingClient(api_key="gemini-key", model="gemini-embedding-001", dimensions=3)

    assert await client.embed_text("title", "content") == [0.1, 0.2, 0.3]
    assert sleep_calls == [0.0]


@pytest.mark.asyncio
async def test_gemini_embedding_falls_back_after_repeated_429(monkeypatch):
    monkeypatch.setattr(embedding_client_module.settings, "embedding_provider", "gemini")
    monkeypatch.setattr(embedding_client_module.settings, "embedding_fallback_enabled", True)
    monkeypatch.setattr(embedding_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(embedding_client_module.asyncio, "sleep", fake_sleep)
    _FakeAsyncClient.responses = [
        _response(429, {"error": "rate limited"}, {"retry-after": "0"}),
        _response(429, {"error": "rate limited"}, {"retry-after": "0"}),
        _response(429, {"error": "rate limited"}, {"retry-after": "0"}),
        _response(429, {"error": "rate limited"}, {"retry-after": "0"}),
    ]

    client = EmbeddingClient(api_key="gemini-key", model="gemini-embedding-001", dimensions=3)

    assert await client.embed_text("title", "abc") == client._fallback_embedding("title\nabc")


@pytest.mark.asyncio
async def test_gemini_embedding_non_retryable_error_raises(monkeypatch):
    monkeypatch.setattr(embedding_client_module.settings, "embedding_provider", "gemini")
    monkeypatch.setattr(embedding_client_module.settings, "embedding_fallback_enabled", True)
    monkeypatch.setattr(embedding_client_module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.responses = [_response(400, {"error": "bad request"})]

    client = EmbeddingClient(api_key="gemini-key", model="gemini-embedding-001", dimensions=3)

    with pytest.raises(httpx.HTTPStatusError):
        await client.embed_text("title", "content")
