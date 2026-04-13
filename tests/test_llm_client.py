from __future__ import annotations

import pytest

from neo.config import Settings
from neo.core.llm import NeoLLMClient, normalize_llm_provider


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "  {\"ok\": true}  "}}]}


class _FakeAsyncClient:
    last_request: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        type(self).last_request = {"url": url, "headers": headers, "json": json}
        return _FakeResponse()


def test_normalize_openai_compatible_aliases() -> None:
    assert normalize_llm_provider("openai") == "openai"
    assert normalize_llm_provider("ollama") == "openai"
    assert normalize_llm_provider("openrouter") == "openai"
    assert normalize_llm_provider("minimax") == "anthropic"


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


def test_provider_defaults_for_common_openai_compatible_servers() -> None:
    ollama = NeoLLMClient(provider="ollama", api_key=None, model="llama3.2")
    openrouter = NeoLLMClient(provider="openrouter", api_key="key", model="anthropic/claude-sonnet-4")
    lmstudio = NeoLLMClient(provider="lmstudio", api_key=None, model="local-model")

    assert ollama.base_url == "http://127.0.0.1:11434/v1"
    assert openrouter.base_url == "https://openrouter.ai/api/v1"
    assert lmstudio.base_url == "http://127.0.0.1:1234/v1"


def test_ollama_configuration_does_not_require_api_key() -> None:
    settings = Settings(_env_file=None, llm_provider="ollama", llm_model="llama3.2")

    assert settings.llm_configured_for("spark") is True
    assert settings.llm_api_key_for("spark") is None
