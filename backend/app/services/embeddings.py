from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
from typing import Sequence

import httpx

from app.core.config import (EMBEDDING_API_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_BATCH_SIZE,
    EMBEDDING_MAX_RETRIES, EMBEDDING_RETRY_BASE_SECONDS, EMBEDDING_RETRY_MAX_SECONDS,
    EMBEDDING_MODEL, EMBEDDING_PROVIDER, EMBEDDING_TIMEOUT_SECONDS,
    GEMINI_EMBEDDING_BATCH_SIZE, GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS,
    LOCAL_ONNX_MODEL_DIR, VECTOR_EMBEDDING_DIMENSION)


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    pass


class EmbeddingRetryableError(EmbeddingProviderError):
    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EmbeddingRateLimitError(EmbeddingRetryableError):
    pass


logger = logging.getLogger(__name__)
LOCAL_ONNX_MODEL_REVISION = "cea84a7cfcd78e04bc1ef6c7182a06fc72a22fbb"
LOCAL_ONNX_MODEL_SHA256 = "84a4d426f7e87a6bf5bf195f0bae2c4a7d15f675b23ca96f42fab8326d7a77aa"
logger.info("Embedding provider=%s model=%s dimension=%s device=%s",
    EMBEDDING_PROVIDER, EMBEDDING_MODEL, VECTOR_EMBEDDING_DIMENSION,
    "CPU" if EMBEDDING_PROVIDER == "local_onnx" else "configured")


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, seconds) if math.isfinite(seconds) else None
        except (TypeError, ValueError, OverflowError):
            return None


def _retry_delay_seconds(attempt: int, retry_after: float | None = None) -> float:
    exponential_cap = min(EMBEDDING_RETRY_MAX_SECONDS,
        EMBEDDING_RETRY_BASE_SECONDS * (2 ** attempt))
    jittered = random.uniform(0.0, exponential_cap)
    return max(jittered, retry_after or 0.0)


async def _pace_gemini_request(previous_request_succeeded: bool) -> None:
    if (EMBEDDING_PROVIDER == "gemini" and previous_request_succeeded
            and GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS > 0):
        await asyncio.sleep(GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS)


async def _post_with_retries(client: httpx.AsyncClient, url: str, *, headers: dict, json: dict) -> httpx.Response:
    for attempt in range(EMBEDDING_MAX_RETRIES + 1):
        try:
            response = await client.post(url, headers=headers, json=json)
        except httpx.RequestError as exc:
            if attempt >= EMBEDDING_MAX_RETRIES:
                raise EmbeddingProviderError(
                    f"Embedding provider request failed after {attempt + 1} attempts ({type(exc).__name__})") from exc
            await asyncio.sleep(_retry_delay_seconds(attempt))
            continue

        retryable_status = response.status_code in {408, 429, 500, 502, 503, 504}
        if retryable_status:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt >= EMBEDDING_MAX_RETRIES:
                message = f"Embedding provider returned HTTP {response.status_code} after {attempt + 1} attempts"
                if retry_after is not None:
                    message += f" (Retry-After: {retry_after:.1f} seconds)"
                if response.status_code == 429:
                    raise EmbeddingRateLimitError(message, retry_after)
                raise EmbeddingRetryableError(message, retry_after)
            await asyncio.sleep(_retry_delay_seconds(attempt, retry_after))
            continue
        response.raise_for_status()
        return response
    raise AssertionError("unreachable retry loop")


def embedding_configuration() -> dict:
    configured = bool(EMBEDDING_PROVIDER and EMBEDDING_MODEL and
        (EMBEDDING_PROVIDER in {"local", "local_onnx"} or EMBEDDING_API_KEY))
    return {"provider": EMBEDDING_PROVIDER or None, "model": EMBEDDING_MODEL or None,
            "dimension": VECTOR_EMBEDDING_DIMENSION, "configured": configured}


@lru_cache(maxsize=1)
def _local_sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingUnavailable("Local embeddings require sentence-transformers; install backend requirements") from exc
    try:
        return SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    except Exception as exc:
        raise EmbeddingUnavailable(f"Could not load local embedding model {EMBEDDING_MODEL} ({type(exc).__name__})") from exc


def _local_embed_sync(texts: Sequence[str], input_type: str) -> list[list[float]]:
    model = _local_sentence_transformer()
    native_dimension = model.get_sentence_embedding_dimension()
    if native_dimension != VECTOR_EMBEDDING_DIMENSION:
        raise EmbeddingProviderError(
            f"Local model dimension {native_dimension} does not match VECTOR_EMBEDDING_DIMENSION={VECTOR_EMBEDDING_DIMENSION}")
    prefix = "query: " if input_type == "query" else "passage: "
    inputs = [prefix + text for text in texts]
    try:
        vectors = model.encode(inputs, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False)
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        return _validated_vectors(vectors, len(texts))
    except EmbeddingProviderError:
        raise
    except Exception as exc:
        raise EmbeddingProviderError(f"Local embedding inference failed ({type(exc).__name__})") from exc


@lru_cache(maxsize=1)
def _local_onnx_runtime():
    """Load the pinned E5 ONNX model with CPUExecutionProvider only."""
    if EMBEDDING_MODEL != "intfloat/multilingual-e5-base":
        raise EmbeddingUnavailable("The local ONNX runtime is pinned to intfloat/multilingual-e5-base")
    if VECTOR_EMBEDDING_DIMENSION != 768:
        raise EmbeddingUnavailable("The local ONNX E5 runtime requires a 768-dimensional vector schema")
    model_path = LOCAL_ONNX_MODEL_DIR / "model.onnx"
    tokenizer_path = LOCAL_ONNX_MODEL_DIR / "tokenizer.json"
    if not model_path.is_file() or not tokenizer_path.is_file():
        raise EmbeddingUnavailable(
            f"Pinned local ONNX model files are missing from {LOCAL_ONNX_MODEL_DIR}")

    digest = hashlib.sha256()
    try:
        with model_path.open("rb") as model_file:
            for block in iter(lambda: model_file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise EmbeddingUnavailable("Could not read the pinned local ONNX model") from exc
    if digest.hexdigest() != LOCAL_ONNX_MODEL_SHA256:
        raise EmbeddingUnavailable("Local ONNX model SHA-256 does not match the pinned E5 artifact")

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=512)
        pad_id = tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise EmbeddingUnavailable("Pinned E5 tokenizer is missing its pad token")
        tokenizer.enable_padding(direction="right", pad_id=pad_id, pad_token="<pad>")
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        if "CPUExecutionProvider" not in session.get_providers():
            raise EmbeddingUnavailable("ONNX runtime did not activate CPUExecutionProvider")
        inputs = {item.name for item in session.get_inputs()}
        if not {"input_ids", "attention_mask"}.issubset(inputs):
            raise EmbeddingUnavailable("Pinned E5 ONNX model has an incompatible input signature")
        logger.info("Loaded pinned local ONNX model=%s revision=%s dimension=768 device=CPU",
            EMBEDDING_MODEL, LOCAL_ONNX_MODEL_REVISION)
        return session, tokenizer
    except EmbeddingUnavailable:
        raise
    except Exception as exc:
        raise EmbeddingUnavailable(
            f"Could not load local ONNX embedding runtime ({type(exc).__name__}: {exc})") from exc


def _local_onnx_embed_sync(texts: Sequence[str], input_type: str) -> list[list[float]]:
    """Embed E5 queries/passages without importing torch or Sentence Transformers."""
    try:
        import numpy as np

        session, tokenizer = _local_onnx_runtime()
        prefix = "query: " if input_type == "query" else "passage: "
        encoded = tokenizer.encode_batch([prefix + text for text in texts], add_special_tokens=True)
        input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
        attention_mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
        output_names = {item.name for item in session.get_outputs()}
        if "last_hidden_state" not in output_names:
            raise EmbeddingProviderError("Pinned E5 ONNX model is missing last_hidden_state output")
        hidden = np.asarray(session.run(["last_hidden_state"], {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        })[0], dtype=np.float32)
        if (hidden.ndim != 3 or hidden.shape[0] != len(texts)
                or hidden.shape[1] != attention_mask.shape[1]
                or hidden.shape[2] != VECTOR_EMBEDDING_DIMENSION):
            raise EmbeddingProviderError("Local ONNX model output shape is incompatible with the 768D E5 vectors")
        mask = attention_mask.astype(np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        if not np.isfinite(norms).all() or (norms <= 0).any():
            raise EmbeddingProviderError("Local ONNX model produced an invalid embedding norm")
        vectors = (pooled / norms).tolist()
        return _validated_vectors(vectors, len(texts))
    except (EmbeddingUnavailable, EmbeddingProviderError):
        raise
    except Exception as exc:
        raise EmbeddingProviderError(f"Local ONNX embedding inference failed ({type(exc).__name__})") from exc


async def embed_query(text: str) -> list[float]:
    vectors = await embed_texts([text], input_type="query")
    return vectors[0]


def _validated_vectors(vectors: object, expected_count: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise EmbeddingProviderError("Embedding provider returned an unexpected batch response")
    validated: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != VECTOR_EMBEDDING_DIMENSION:
            raise EmbeddingProviderError("Embedding dimension does not match VECTOR_EMBEDDING_DIMENSION")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError("Embedding provider returned non-numeric values") from exc
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingProviderError("Embedding contains a non-finite value")
        validated.append(values)
    return validated


async def embed_texts(texts: Sequence[str], *, input_type: str = "document") -> list[list[float]]:
    if not texts:
        return []
    if input_type not in {"document", "query"}:
        raise ValueError("input_type must be 'document' or 'query'")
    if not EMBEDDING_PROVIDER or not EMBEDDING_MODEL:
        raise EmbeddingUnavailable("Embedding provider and model must be configured")
    if EMBEDDING_PROVIDER not in {"local", "local_onnx"} and not EMBEDDING_API_KEY:
        raise EmbeddingUnavailable("Embedding provider, model, and API key must be configured")
    if EMBEDDING_PROVIDER not in {"local", "local_onnx", "openai", "openai_compatible", "gemini"}:
        raise EmbeddingUnavailable(f"Unsupported embedding provider: {EMBEDDING_PROVIDER}")
    if EMBEDDING_PROVIDER == "local":
        return await asyncio.to_thread(_local_embed_sync, list(texts), input_type)
    if EMBEDDING_PROVIDER == "local_onnx":
        return await asyncio.to_thread(_local_onnx_embed_sync, list(texts), input_type)
    all_vectors: list[list[float]] = []
    timeout = httpx.Timeout(EMBEDDING_TIMEOUT_SECONDS)
    batch_size = GEMINI_EMBEDDING_BATCH_SIZE if EMBEDDING_PROVIDER == "gemini" else EMBEDDING_BATCH_SIZE
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for offset in range(0, len(texts), batch_size):
                await _pace_gemini_request(offset > 0)
                batch = list(texts[offset:offset + batch_size])
                if EMBEDDING_PROVIDER == "gemini":
                    model_name = EMBEDDING_MODEL.removeprefix("models/")
                    response = await _post_with_retries(client,
                        f"{EMBEDDING_API_BASE_URL}/models/{model_name}:batchEmbedContents",
                        headers={"x-goog-api-key": EMBEDDING_API_KEY},
                        json={"requests": [{
                            "model": f"models/{model_name}",
                            "content": {"parts": [{"text": item}]},
                            "embedContentConfig": {"outputDimensionality": VECTOR_EMBEDDING_DIMENSION},
                        } for item in batch]},
                    )
                else:
                    response = await _post_with_retries(client, f"{EMBEDDING_API_BASE_URL}/embeddings",
                        headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
                        json={"model": EMBEDDING_MODEL, "input": batch})
                payload = response.json()
                if not isinstance(payload, dict):
                    raise EmbeddingProviderError("Embedding provider returned a malformed response")
                if EMBEDDING_PROVIDER == "gemini":
                    data = payload.get("embeddings")
                    if not isinstance(data, list):
                        raise EmbeddingProviderError("Gemini returned a malformed embedding response")
                    vectors = [item.get("values") if isinstance(item, dict) else None for item in data]
                else:
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise EmbeddingProviderError("Embedding provider returned a malformed response")
                    try:
                        data.sort(key=lambda item: item.get("index", 0))
                        vectors = [item.get("embedding") if isinstance(item, dict) else None for item in data]
                    except (AttributeError, TypeError) as exc:
                        raise EmbeddingProviderError("Embedding provider returned a malformed response") from exc
                all_vectors.extend(_validated_vectors(vectors, len(batch)))
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, EmbeddingProviderError):
            raise
        raise EmbeddingProviderError(f"Embedding provider request failed ({type(exc).__name__})") from exc
    return all_vectors
