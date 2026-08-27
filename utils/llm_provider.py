"""Multi-provider chat adapter with ordered failover."""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import AI_PROVIDERS, GROQ_API_KEY, GROQ_MODEL


def _providers() -> list[dict[str, Any]]:
    if AI_PROVIDERS:
        return [dict(p) for p in AI_PROVIDERS]
    if GROQ_API_KEY:
        return [{"name": "groq", "type": "openai", "api_key": GROQ_API_KEY,
                 "base_url": "https://api.groq.com/openai/v1", "model": GROQ_MODEL}]
    return []


def configured_provider_count() -> int:
    return len(_providers())


def _http_error_message(exc: HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", errors="replace")
        return f"HTTP {exc.code}: {detail[:1000]}"
    except Exception:
        return f"HTTP {exc.code}: {exc.reason}"


def _base_url(provider: dict) -> str | None:
    if provider.get("base_url"):
        return provider["base_url"]
    if str(provider.get("type", "")).lower() in {"openai", "openai-compatible"}:
        return "https://api.openai.com/v1"
    return None


def _is_groq(provider: dict) -> bool:
    name = str(provider.get("name", "")).lower()
    url = str(provider.get("base_url", "")).lower()
    return name == "groq" or "api.groq.com" in url


def _groq_call(provider: dict, messages: list[dict], system: str, max_tokens: int, temperature: float) -> str:
    """Use the official Groq SDK; it supplies the headers Cloudflare expects."""
    from groq import Groq

    base_url = provider.get("base_url")
    if base_url and str(base_url).rstrip("/").endswith("/openai/v1"):
        # Groq SDK appends /openai/v1 itself.
        base_url = str(base_url).rstrip("/")[:-len("/openai/v1")]
    kwargs = {"api_key": provider.get("api_key", "")}
    if base_url:
        kwargs["base_url"] = base_url
    client = Groq(**kwargs)
    response = client.chat.completions.create(
        model=provider.get("model", GROQ_MODEL),
        messages=[{"role": "system", "content": system}] + messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def _openai_compatible_call(provider: dict, messages: list[dict], system: str, max_tokens: int, temperature: float) -> str:
    """Call an OpenAI-compatible endpoint directly.

    The Groq SDK hard-codes `/openai/v1/chat/completions`, so using it for
    custom base URLs can produce `/openai/v1/openai/v1/...`. Direct HTTP keeps
    Groq, OpenAI, OpenRouter, Ollama and OpenCode endpoint paths correct.
    """
    base_url = _base_url(provider) or "https://api.openai.com/v1"
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = json.dumps({
        "model": provider.get("model", GROQ_MODEL),
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }).encode()
    req = Request(url, data=body, headers={
        "Authorization": f"Bearer {provider.get('api_key', '')}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/idx-analyst-bot",
        "X-Title": "IDX Analyst Bot",
    })
    try:
        with urlopen(req, timeout=float(provider.get("timeout", 60))) as response:
            data = json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(_http_error_message(exc)) from exc
    return data["choices"][0]["message"]["content"]


def _anthropic_call(provider: dict, messages: list[dict], system: str, max_tokens: int, temperature: float) -> str:
    body = json.dumps({
        "model": provider["model"], "max_tokens": max_tokens,
        "temperature": temperature, "system": system,
        "messages": [m for m in messages if m.get("role") != "system"],
    }).encode()
    url = provider.get("base_url", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    req = Request(url, data=body, headers={
        "x-api-key": provider.get("api_key", ""),
        "anthropic-version": "2023-06-01", "content-type": "application/json",
    })
    try:
        with urlopen(req, timeout=float(provider.get("timeout", 60))) as response:
            data = json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(_http_error_message(exc)) from exc
    return data["content"][0]["text"]


def chat_completion(messages: list[dict], system: str, max_tokens: int, temperature: float) -> tuple[str, str]:
    errors = []
    for provider in _providers():
        name = str(provider.get("name") or provider.get("type") or "provider")
        try:
            if str(provider.get("type", "openai")).lower() in {"anthropic", "claude"}:
                text = _anthropic_call(provider, messages, system, max_tokens, temperature)
            elif _is_groq(provider):
                text = _groq_call(provider, messages, system, max_tokens, temperature)
            else:
                text = _openai_compatible_call(provider, messages, system, max_tokens, temperature)
            result = str(text or "").strip()
            if not result or result.lower() == "none":
                raise ValueError("Respon AI kosong dari provider")
            return result, name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors) if errors else "belum ada AI provider yang dikonfigurasi")
