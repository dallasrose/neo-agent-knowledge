from __future__ import annotations

import pytest

from neo.config import Settings
from neo.core.llm import NeoLLMClient, _collect_text, normalize_llm_provider


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "  {\"ok\": true}  "}}]}


class _FakeGeminiResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"candidates": [{"content": {"parts": [{"text": "  {\"ok\": true}  "}]}}]}


class _FakeAsyncClient:
    last_request: dict | None = None
    response_cls = _FakeResponse

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        type(self).last_request = {"url": url, **kwargs}
        return self.response_cls()


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _ContentBlock:
    def __init__(self, content) -> None:
        self.content = content


class _EmptyContentWithDump:
    content = []

    def model_dump(self) -> dict:
        return {"provider_response": {"message": {"content": [{"type": "text", "text": "dumped text"}]}}}


def test_normalize_openai_compatible_aliases() -> None:
    assert normalize_llm_provider("openai") == "openai"
    assert normalize_llm_provider("ollama") == "openai"
    assert normalize_llm_provider("openrouter") == "openai"
    assert normalize_llm_provider("minimax-openai") == "openai"
    assert normalize_llm_provider("minimax-chat-completions") == "openai"
    assert normalize_llm_provider("minimax") == "anthropic"
    assert normalize_llm_provider("minimax-anthropic") == "anthropic"
    assert normalize_llm_provider("gemini") == "gemini"
    assert normalize_llm_provider("google") == "gemini"


def test_collect_text_handles_anthropic_compatible_content_shapes() -> None:
    assert _collect_text([_TextBlock(" alpha "), {"text": "beta"}, _ContentBlock([{"content": "gamma"}])]) == "alpha\nbeta\ngamma"


def test_collect_text_falls_back_to_model_dump_when_content_is_empty() -> None:
    assert _collect_text(_EmptyContentWithDump()) == "dumped text"


@pytest.mark.asyncio
async def test_openai_compatible_call_uses_httpx_without_sdk(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    llm = NeoLLMClient(
        provider="ollama",
        api_key=None,
        model="llama3.2",
        base_url="http://127.0.0.1:11434/v1",
    )

    result = await llm.call("return json", max_tokens=128)

    assert result == '{"ok": true}'
    assert _FakeAsyncClient.last_request == {
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer ollama",
        },
        "json": {
            "model": "llama3.2",
            "messages": [{"role": "user", "content": "return json"}],
            "max_tokens": 128,
            "temperature": 0,
        },
    }


@pytest.mark.asyncio
async def test_gemini_call_uses_generate_content_endpoint(monkeypatch) -> None:
    import httpx

    class GeminiClient(_FakeAsyncClient):
        response_cls = _FakeGeminiResponse

    monkeypatch.setattr(httpx, "AsyncClient", GeminiClient)
    llm = NeoLLMClient(provider="gemini", api_key="key", model="gemini-2.5-flash")

    result = await llm.call("return json", max_tokens=128)

    assert result == '{"ok": true}'
    assert GeminiClient.last_request == {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        "params": {"key": "key"},
        "json": {
            "contents": [{"role": "user", "parts": [{"text": "return json"}]}],
            "generationConfig": {
                "maxOutputTokens": 128,
                "temperature": 0,
            },
        },
    }


def test_provider_defaults_for_common_openai_compatible_servers() -> None:
    ollama = NeoLLMClient(provider="ollama", api_key=None, model="llama3.2")
    openrouter = NeoLLMClient(provider="openrouter", api_key="key", model="anthropic/claude-sonnet-4")
    minimax_openai = NeoLLMClient(provider="minimax-openai", api_key="key", model="MiniMax-M2.7")
    lmstudio = NeoLLMClient(provider="lmstudio", api_key=None, model="local-model")
    gemini = NeoLLMClient(provider="gemini", api_key="key", model="gemini-2.5-flash")

    assert ollama.base_url == "http://127.0.0.1:11434/v1"
    assert openrouter.base_url == "https://openrouter.ai/api/v1"
    assert minimax_openai.base_url == "https://api.minimax.io/v1"
    assert lmstudio.base_url == "http://127.0.0.1:1234/v1"
    assert gemini.base_url == "https://generativelanguage.googleapis.com/v1beta"


def test_ollama_configuration_does_not_require_api_key() -> None:
    settings = Settings(_env_file=None, llm_provider="ollama", llm_model="llama3.2")

    assert settings.llm_configured_for("spark") is True
    assert settings.llm_api_key_for("spark") is None


def test_task_specific_model_config_supports_research_ingestion_recall_and_rerank() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_model="qwen2.5:7b",
        llm_research_provider="gemini",
        llm_research_model="gemini-2.5-flash",
        llm_research_api_key="google-key",
        llm_ingestion_provider="ollama",
        llm_ingestion_model="qwen2.5:7b",
        llm_recall_provider="lmstudio",
        llm_recall_model="local-reranker",
        llm_recall_base_url="http://localhost:1234/v1",
        llm_rerank_provider="openrouter",
        llm_rerank_model="google/gemini-flash",
        llm_rerank_api_key="router-key",
    )

    assert settings.llm_provider_for("research") == "gemini"
    assert settings.llm_model_for("research") == "gemini-2.5-flash"
    assert settings.llm_api_key_for("research") == "google-key"
    assert settings.llm_provider_for("ingestion") == "ollama"
    assert settings.llm_model_for("ingestion") == "qwen2.5:7b"
    assert settings.llm_configured_for("ingestion") is True
    assert settings.llm_configured_for("recall") is True
    assert settings.llm_provider_for("rerank") == "openrouter"
    assert settings.llm_api_key_for("rerank") == "router-key"


def test_spark_fallback_does_not_configure_research_lane() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="",
        llm_model="",
        llm_api_key=None,
        llm_base_url=None,
        llm_spark_api_key="spark-key",
        llm_spark_base_url="https://example.test/anthropic",
    )

    assert settings.llm_configured_for("spark") is True
    assert settings.llm_configured_for("resolution") is True
    assert settings.llm_configured_for("research") is False
    assert settings.llm_model_for("research") == ""
    assert settings.llm_api_key_for("research") is None
