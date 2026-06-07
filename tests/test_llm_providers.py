from __future__ import annotations

from neo.core.llm import NeoLLMClient, normalize_llm_provider


def test_xai_and_grok_aliases_use_xai_openai_compatible_endpoint():
    for provider in ("xai", "x-ai", "grok"):
        client = NeoLLMClient(api_key="test", model="grok-4-mini", provider=provider)
        assert normalize_llm_provider(provider) == "openai"
        assert client.base_url == "https://api.x.ai/v1"


def test_deepseek_alias_uses_openai_compatible_endpoint():
    client = NeoLLMClient(api_key="test", model="deepseek-chat", provider="deepseek")
    assert normalize_llm_provider("deepseek") == "openai"
    assert client.base_url == "https://api.deepseek.com/v1"


def test_gemma_viable_through_local_or_openai_compatible_endpoints():
    ollama = NeoLLMClient(api_key=None, model="gemma3:latest", provider="ollama")
    assert ollama.base_url == "http://127.0.0.1:11434/v1"

    custom = NeoLLMClient(
        api_key="test",
        model="google/gemma-3-27b-it",
        provider="openai-compatible",
        base_url="https://openrouter.ai/api/v1",
    )
    assert custom.base_url == "https://openrouter.ai/api/v1"
