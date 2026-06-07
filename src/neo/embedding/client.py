from __future__ import annotations

import asyncio
from typing import Sequence

import httpx
import tiktoken

from neo.config import settings


class EmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self.api_key = api_key or settings.embedding_api_key
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self._encoder = tiktoken.get_encoding("cl100k_base")
        use_mock_provider = settings.embedding_provider == "mock"
        self._fallback_enabled = settings.embedding_fallback_enabled or use_mock_provider
        self.provider = (settings.embedding_provider or "openai").strip().lower()
        self._client = None
        if self.api_key and not use_mock_provider and self.provider in {"openai", "openai-compatible", "openai_compatible"}:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI embeddings require the 'openai' package, which is included "
                    "by default. Reinstall or upgrade neo-agent-knowledge."
                ) from exc
            self._client = AsyncOpenAI(api_key=self.api_key)
        elif self.api_key and not use_mock_provider and self.provider in {"gemini", "google", "google-gemini"}:
            self._client = "gemini"
        elif self.api_key and not use_mock_provider:
            raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")

    def prepare_text(self, title: str, content: str, max_tokens: int = 8191) -> str:
        combined = f"{title}\n{content}".strip()
        tokens = self._encoder.encode(combined)
        if len(tokens) <= max_tokens:
            return combined
        return self._encoder.decode(tokens[:max_tokens])

    async def embed_text(self, title: str, content: str) -> list[float]:
        prepared = self.prepare_text(title, content)
        if self._client is None:
            if not self._fallback_enabled:
                raise RuntimeError("Embedding API key missing and fallback embeddings disabled")
            return self._fallback_embedding(prepared)
        if self._client == "gemini":
            return await self._embed_gemini(prepared)
        response = await self._client.embeddings.create(
            model=self.model,
            input=prepared,
            dimensions=self.dimensions,
        )
        return list(response.data[0].embedding)

    async def embed_batch(self, documents: Sequence[tuple[str, str]]) -> list[list[float]]:
        if self._client is None:
            if not self._fallback_enabled:
                raise RuntimeError("Embedding API key missing and fallback embeddings disabled")
            return [self._fallback_embedding(self.prepare_text(title, content)) for title, content in documents]
        prepared = [self.prepare_text(title, content) for title, content in documents]
        if self._client == "gemini":
            return [await self._embed_gemini(text) for text in prepared]
        response = await self._client.embeddings.create(
            model=self.model,
            input=prepared,
            dimensions=self.dimensions,
        )
        return [list(item.embedding) for item in response.data]

    async def _embed_gemini(self, text: str) -> list[float]:
        payload = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": self.dimensions,
        }
        try:
            data = await self._post_gemini_embedding(payload)
        except httpx.HTTPStatusError as exc:
            if self._fallback_enabled and self._is_retryable_status(exc.response.status_code):
                return self._fallback_embedding(text)
            raise
        values = data.get("embedding", {}).get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("No embedding values in Gemini response")
        return [float(value) for value in values]

    async def _post_gemini_embedding(self, payload: dict) -> dict:
        last_error: httpx.HTTPStatusError | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(4):
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent",
                    params={"key": self.api_key},
                    json=payload,
                )
                try:
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    if not self._is_retryable_status(response.status_code):
                        raise
                    last_error = exc
                    if attempt == 3:
                        break
                    await asyncio.sleep(self._retry_delay(response, attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini embedding request failed without an HTTP response")

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code < 600

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 10.0)
            except ValueError:
                pass
        return min(0.5 * (2**attempt), 5.0)

    def _fallback_embedding(self, text: str) -> list[float]:
        values = [float((ord(char) % 23) / 23) for char in text[: self.dimensions]]
        if len(values) < self.dimensions:
            values.extend([0.0] * (self.dimensions - len(values)))
        return values
