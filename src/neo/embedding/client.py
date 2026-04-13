from __future__ import annotations

from typing import Sequence

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
        self._client = None
        if self.api_key and not use_mock_provider:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI embeddings require the 'openai' package, which is included "
                    "by default. Reinstall or upgrade neo-agent-knowledge."
                ) from exc
            self._client = AsyncOpenAI(api_key=self.api_key)

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
        response = await self._client.embeddings.create(
            model=self.model,
            input=prepared,
            dimensions=self.dimensions,
        )
        return [list(item.embedding) for item in response.data]

    def _fallback_embedding(self, text: str) -> list[float]:
        values = [float((ord(char) % 23) / 23) for char in text[: self.dimensions]]
        if len(values) < self.dimensions:
            values.extend([0.0] * (self.dimensions - len(values)))
        return values
