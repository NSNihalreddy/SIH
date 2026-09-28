"""Gemini's asynchronous embedding Batch API, used only for document indexing."""
from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

from app.core.config import (EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_TIMEOUT_SECONDS,
    GEMINI_BATCH_CHUNK_SIZE, GEMINI_BATCH_POLL_SECONDS,
    VECTOR_EMBEDDING_DIMENSION)
from app.services.embeddings import EmbeddingProviderError

POLL_SECONDS = GEMINI_BATCH_POLL_SECONDS
BATCH_CHUNK_SIZE = GEMINI_BATCH_CHUNK_SIZE


def _value(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, obj.get("".join([key.split("_")[0], *[p.title() for p in key.split("_")[1:]]]), default))
    return getattr(obj, key, default)


def _default_client():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise EmbeddingProviderError("Gemini Batch API requires google-genai; install backend requirements") from exc
    return genai.Client(api_key=EMBEDDING_API_KEY,
        http_options=types.HttpOptions(timeout=int(EMBEDDING_TIMEOUT_SECONDS * 1000)))


def _vectors_from_job(job: Any, expected: int, dimension: int) -> list[list[float]]:
    dest = _value(job, "dest") or _value(_value(job, "response", {}), "dest") or _value(_value(job, "response", {}), "destination")
    responses = (_value(dest, "inlined_embed_content_responses") or
        _value(dest, "inlined_responses") or _value(_value(job, "response", {}), "inlined_embed_content_responses"))
    if not isinstance(responses, (list, tuple)) or len(responses) != expected:
        raise EmbeddingProviderError(f"Gemini Batch API returned {len(responses) if isinstance(responses, (list, tuple)) else 'malformed'} results for {expected} requests")
    result = []
    for response in responses:
        error = _value(response, "error")
        if error:
            raise EmbeddingProviderError(f"Gemini batch item failed: {str(error)[:300]}")
        body = _value(response, "response") or response
        embedding = _value(body, "embedding")
        values = _value(embedding, "values") if embedding is not None else None
        if not isinstance(values, (list, tuple)) or len(values) != dimension:
            raise EmbeddingProviderError(f"Gemini batch vector must contain exactly {dimension} dimensions")
        try:
            vector = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError("Gemini batch returned non-numeric vector values") from exc
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingProviderError("Gemini batch returned non-finite vector values")
        result.append(vector)
    return result


async def _await_batch(client: Any, name: str, expected: int, dimension: int,
                       sleep: Callable = asyncio.sleep) -> tuple[str, list[list[float]] | None, str | None]:
    batch = await asyncio.to_thread(client.batches.get, name=name)
    raw_state = _value(batch, "state") or _value(_value(batch, "metadata", {}), "state") or ""
    state = str(getattr(raw_state, "name", raw_state)).upper().replace("JOB_STATE_", "")
    if state in {"PENDING", "QUEUED", "RUNNING", "STATE_UNSPECIFIED"}:
        return "RUNNING", None, None
    if state in {"CANCELLED", "CANCELED"}:
        return "CANCELLED", None, "Gemini batch was cancelled"
    if state == "EXPIRED":
        return "EXPIRED", None, "Gemini batch expired before completion"
    if state == "FAILED":
        error = _value(batch, "error") or _value(_value(batch, "response", {}), "error")
        return "FAILED", None, f"Gemini batch failed: {str(error or 'provider reported failure')[:400]}"
    if state not in {"SUCCEEDED", "COMPLETED"}:
        return "FAILED", None, f"Gemini batch returned unrecognized terminal state: {state or 'unknown'}"
    try:
        vectors = _vectors_from_job(batch, expected, dimension)
    except EmbeddingProviderError as exc:
        return "FAILED", None, str(exc)
    return "SUCCEEDED", vectors, None


async def create_embedding_batch(texts: list[str], *, client_factory: Callable | None = None) -> str:
    if not texts:
        raise ValueError("Cannot submit an empty Gemini embedding batch")
    client = (client_factory or _default_client)()
    try:
        source = {"inlined_requests": {
            "contents": [{"parts": [{"text": text}]} for text in texts],
            "config": {"output_dimensionality": VECTOR_EMBEDDING_DIMENSION},
        }}
        created = await asyncio.to_thread(client.batches.create_embeddings,
            model=EMBEDDING_MODEL, src=source,
            config={"display_name": "data-mine-document-indexing"})
        name = _value(created, "name")
        if not name:
            raise EmbeddingProviderError("Gemini Batch API submission did not return a batch identifier")
        return str(name)
    except EmbeddingProviderError:
        raise
    except Exception as exc:
        raise EmbeddingProviderError(f"Gemini Batch API submission failed ({type(exc).__name__}): {str(exc)[:300]}") from exc


async def poll_embedding_batch(name: str, expected: int, *, client_factory: Callable | None = None):
    client = (client_factory or _default_client)()
    try:
        state, vectors, error = await _await_batch(client, name, expected, VECTOR_EMBEDDING_DIMENSION)
        return {"state": state, "vectors": vectors, "error": error}
    except Exception as exc:
        raise EmbeddingProviderError(f"Gemini Batch API status request failed ({type(exc).__name__}): {str(exc)[:300]}") from exc


def chunk_groups(items: list[Any], size: int = BATCH_CHUNK_SIZE):
    for start in range(0, len(items), size):
        yield items[start:start + size]
