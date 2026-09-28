from __future__ import annotations

import httpx

from app.core.config import (LLM_API_BASE_URL, LLM_API_KEY, LLM_MAX_TOKENS, LLM_MODEL,
    LLM_PROVIDER, LLM_TEMPERATURE, LLM_TIMEOUT_SECONDS)


class LLMUnavailable(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


def llm_configuration() -> dict:
    return {"provider": LLM_PROVIDER or None, "model": LLM_MODEL or None,
            "configured": bool(LLM_PROVIDER and LLM_MODEL and LLM_API_KEY)}


async def generate_answer(system_prompt: str, user_prompt: str) -> str:
    if not LLM_PROVIDER or not LLM_MODEL or not LLM_API_KEY:
        raise LLMUnavailable("LLM provider, model, and API key must be configured")
    if LLM_PROVIDER not in {"openai", "openai_compatible", "gemini"}:
        raise LLMUnavailable(f"Unsupported LLM provider: {LLM_PROVIDER}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(LLM_TIMEOUT_SECONDS)) as client:
            if LLM_PROVIDER == "gemini":
                response = await client.post(
                    f"{LLM_API_BASE_URL.rstrip('/')}/models/{LLM_MODEL}:generateContent",
                    headers={"x-goog-api-key": LLM_API_KEY, "Content-Type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": {
                            "temperature": LLM_TEMPERATURE,
                            "maxOutputTokens": LLM_MAX_TOKENS,
                        },
                    },
                )
            else:
                response = await client.post(f"{LLM_API_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                    json={"model": LLM_MODEL, "temperature": LLM_TEMPERATURE, "max_tokens": LLM_MAX_TOKENS,
                          "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]})
            response.raise_for_status()
            payload = response.json()
            if LLM_PROVIDER == "gemini":
                parts = payload["candidates"][0]["content"]["parts"]
                text = "".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
            else:
                text = payload["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise LLMProviderError(f"{LLM_PROVIDER} provider returned an empty response")
            return text.strip()
    except LLMProviderError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        status = f" HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
        raise LLMProviderError(f"{LLM_PROVIDER} provider request failed{status} ({type(exc).__name__})") from exc
