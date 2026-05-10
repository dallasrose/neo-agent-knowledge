from __future__ import annotations

from typing import Any

import httpx


_OPENAI_COMPATIBLE = {
    "openai",
    "openai-compatible",
    "openai_compatible",
    "openrouter",
    "minimax-openai",
    "minimax_chat_completions",
    "minimax-chat-completions",
    "ollama",
    "lmstudio",
    "lm-studio",
    "vllm",
    "llama.cpp",
}

_ANTHROPIC_COMPATIBLE = {
    "anthropic",
    "anthropic-compatible",
    "anthropic_compatible",
    "minimax",
    "minimax-anthropic",
    "minimax_anthropic",
}

_GEMINI_COMPATIBLE = {
    "gemini",
    "google",
    "google-gemini",
}


def normalize_llm_provider(provider: str | None) -> str:
    value = (provider or "anthropic").strip().lower()
    if value in _OPENAI_COMPATIBLE:
        return "openai"
    if value in _ANTHROPIC_COMPATIBLE:
        return "anthropic"
    if value in _GEMINI_COMPATIBLE:
        return "gemini"
    raise ValueError(
        "LLM provider must be one of: anthropic, gemini, google, minimax, "
        "minimax-anthropic, minimax-openai, openai, openai-compatible, "
        "openrouter, ollama, lmstudio, vllm"
    )


def _collect_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        direct = _collect_text(value.get("text") or value.get("content"))
        if direct:
            return direct
        parts = [_collect_text(item) for item in value.values()]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(_collect_text(item))
            else:
                text = getattr(item, "text", None)
                content = getattr(item, "content", None)
                parts.append(_collect_text(text if text is not None else content))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    text = getattr(value, "text", None)
    if text is not None:
        collected = _collect_text(text)
        if collected:
            return collected
    content = getattr(value, "content", None)
    if content is not None:
        collected = _collect_text(content)
        if collected:
            return collected
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _collect_text(model_dump())
        except Exception:
            return ""
    return ""


class NeoLLMClient:
    """Small provider-normalizing LLM client for Neo's JSON-oriented tasks.

    Anthropic-compatible endpoints use the Anthropic SDK when installed.
    OpenAI-compatible endpoints use Neo's required httpx dependency, so Ollama,
    LM Studio, vLLM, llama.cpp servers, OpenRouter, and OpenAI do not require
    an extra Python SDK.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        provider: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        raw_provider = (provider or "anthropic").strip().lower()
        self.provider = normalize_llm_provider(raw_provider)
        local_openai = raw_provider in {"ollama", "lmstudio", "lm-studio", "vllm", "llama.cpp"}
        self.api_key = api_key or ("ollama" if local_openai else None)
        self.model = model
        self.timeout = timeout
        if self.provider == "openai":
            if base_url:
                resolved_base_url = base_url
            elif raw_provider == "openrouter":
                resolved_base_url = "https://openrouter.ai/api/v1"
            elif raw_provider in {"minimax-openai", "minimax-chat-completions", "minimax_chat_completions"}:
                resolved_base_url = "https://api.minimax.io/v1"
            elif raw_provider == "ollama":
                resolved_base_url = "http://127.0.0.1:11434/v1"
            elif raw_provider in {"lmstudio", "lm-studio"}:
                resolved_base_url = "http://127.0.0.1:1234/v1"
            else:
                resolved_base_url = "https://api.openai.com/v1"
            self.base_url = resolved_base_url.rstrip("/")
        elif self.provider == "gemini":
            self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        else:
            self.base_url = base_url
            self._anthropic_client = None

    async def call(self, prompt: str, max_tokens: int = 1024) -> str:
        if self.provider == "openai":
            return await self._call_openai_compatible(prompt, max_tokens=max_tokens)
        if self.provider == "gemini":
            return await self._call_gemini(prompt, max_tokens=max_tokens)
        return await self._call_anthropic_compatible(prompt, max_tokens=max_tokens)

    async def _call_openai_compatible(self, prompt: str, *, max_tokens: int) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("No choices in OpenAI-compatible LLM response")
        message = choices[0].get("message") or {}
        text = _collect_text(message.get("content"))
        if not text:
            raise ValueError("No text content in OpenAI-compatible LLM response")
        return text

    async def _call_gemini(self, prompt: str, *, max_tokens: int) -> str:
        if not self.api_key:
            raise ValueError("Gemini LLM provider requires an API key")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                params={"key": self.api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("No candidates in Gemini LLM response")
        content = candidates[0].get("content") or {}
        text = _collect_text(content.get("parts"))
        if not text:
            raise ValueError("No text content in Gemini LLM response")
        return text

    async def _call_anthropic_compatible(self, prompt: str, *, max_tokens: int) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "Anthropic-compatible LLMs require the 'anthropic' package, which is "
                "included by default. Reinstall or upgrade neo-agent-knowledge."
            ) from exc

        if self._anthropic_client is None:
            self._anthropic_client = anthropic.AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        response = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = _collect_text(response.content)
        if not text:
            raise ValueError("No text block in Anthropic-compatible LLM response")
        return text
