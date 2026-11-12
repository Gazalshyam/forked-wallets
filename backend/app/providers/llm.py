"""LLM provider interface: mock (default), anthropic, openai, groq. Direct HTTP via httpx, no vendor SDK.

Output is untrusted text. Callers validate it (Pydantic) and fall back to deterministic code on any problem.
"""
import json
import logging
import re

import httpx
from pydantic import BaseModel, ValidationError

from ..config import get_settings

log = logging.getLogger("llm")
TIMEOUT = httpx.Timeout(25.0)


def provider_name() -> str:
    s = get_settings()
    if s.llm_provider == "anthropic" and s.anthropic_api_key and s.llm_model:
        return "anthropic"
    if s.llm_provider == "openai" and s.openai_api_key and s.llm_model:
        return "openai"
    if s.llm_provider == "groq" and s.groq_api_key and s.llm_model:
        return "groq"
    return "mock"


def label() -> str:
    p = provider_name()
    return "mock template" if p == "mock" else f"{p}:{get_settings().llm_model}"


def _request(system: str, user: str, max_tokens: int) -> tuple[str, dict, dict]:
    s = get_settings()
    if provider_name() == "anthropic":
        return ("https://api.anthropic.com/v1/messages",
                {"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                {"model": s.llm_model, "max_tokens": max_tokens, "system": system,
                 "messages": [{"role": "user", "content": user}]})
    if provider_name() == "groq":
        return ("https://api.groq.com/openai/v1/chat/completions",
                {"Authorization": f"Bearer {s.groq_api_key}", "content-type": "application/json"},
                {"model": s.llm_model, "max_tokens": max_tokens,
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return ("https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {s.openai_api_key}", "content-type": "application/json"},
            {"model": s.llm_model, "max_completion_tokens": max_tokens,
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})


def _parse(body: dict) -> str | None:
    if provider_name() == "anthropic":
        parts = [c.get("text", "") for c in body.get("content", []) if c.get("type") == "text"]
        return "".join(parts).strip() or None
    choices = body.get("choices") or []
    return (choices[0].get("message", {}).get("content") or "").strip() or None if choices else None


def complete(system: str, user: str, max_tokens: int = 300) -> str | None:
    """Sync call. Returns None in mock mode or on any error (callers fall back)."""
    if provider_name() == "mock":
        return None
    url, headers, body = _request(system, user, max_tokens)
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT)
        if r.status_code >= 400:
            log.warning("LLM HTTP %s: %s", r.status_code, r.text[:300])
            return None
        return _parse(r.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("LLM call failed: %s", e)
        return None


async def acomplete(system: str, user: str, max_tokens: int = 200) -> str | None:
    if provider_name() == "mock":
        return None
    url, headers, body = _request(system, user, max_tokens)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log.warning("LLM HTTP %s: %s", r.status_code, r.text[:300])
            return None
        return _parse(r.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("LLM call failed: %s", e)
        return None


def extract_json(text: str | None, model: type[BaseModel]) -> BaseModel | None:
    """Find the first JSON object in the text and validate it. None if anything is off."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return model.model_validate(json.loads(m.group(0)))
    except (ValueError, ValidationError) as e:
        log.warning("LLM JSON rejected: %s", str(e)[:200])
        return None
