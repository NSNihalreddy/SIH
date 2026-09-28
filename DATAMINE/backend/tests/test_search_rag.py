import asyncio
import uuid
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.chunking import chunk_page, _split
from app.services.embeddings import (EmbeddingRateLimitError, EmbeddingUnavailable,
    embedding_configuration, embed_texts)
from app.services.llm import LLMUnavailable, generate_answer, llm_configuration


def _mock_embedding_client(monkeypatch, response_factory, requests_seen):
    import app.services.embeddings as embeddings
    real_client = httpx.AsyncClient

    def client_factory(*, timeout):
        async def handler(request):
            requests_seen.append(request)
            return response_factory(request)
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", client_factory)


def _configure_embedding_provider(monkeypatch, provider, *, model="gemini-embedding-2", batch_size=8):
    import app.services.embeddings as embeddings
    monkeypatch.setattr(embeddings, "EMBEDDING_PROVIDER", provider)
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", model)
    monkeypatch.setattr(embeddings, "EMBEDDING_API_KEY", "unit-test-key")
    monkeypatch.setattr(embeddings, "EMBEDDING_API_BASE_URL", "https://embedding.test/v1beta")
    monkeypatch.setattr(embeddings, "EMBEDDING_BATCH_SIZE", batch_size)
    monkeypatch.setattr(embeddings, "GEMINI_EMBEDDING_BATCH_SIZE", batch_size)
    monkeypatch.setattr(embeddings, "GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS", 0.0)


def _gemini_payload(count, dimension=768):
    return {"embeddings": [{"values": [0.25] * dimension} for _ in range(count)]}


def test_gemini_embedding_configuration(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini")
    config = embeddings.embedding_configuration()
    assert config == {"provider": "gemini", "model": "gemini-embedding-2", "dimension": 768, "configured": True}
    assert "api_key" not in config


def test_gemini_embedding_batches_parse_values_and_request_768_dimensions(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini", batch_size=2)
    requests_seen = []
    _mock_embedding_client(monkeypatch, lambda request: httpx.Response(
        200, json=_gemini_payload(len(__import__("json").loads(request.content)["requests"]))), requests_seen)

    vectors = asyncio.run(embeddings.embed_texts(["first", "second", "third"]))

    assert len(vectors) == 3 and all(len(vector) == 768 for vector in vectors)
    assert [request.url.path for request in requests_seen] == [
        "/v1beta/models/gemini-embedding-2:batchEmbedContents",
        "/v1beta/models/gemini-embedding-2:batchEmbedContents",
    ]
    assert requests_seen[0].headers["x-goog-api-key"] == "unit-test-key"
    body = __import__("json").loads(requests_seen[0].content)
    assert len(body["requests"]) == 2
    assert body["requests"][0]["content"]["parts"][0]["text"] == "first"
    assert body["requests"][0]["embedContentConfig"]["outputDimensionality"] == 768


def test_gemini_embedding_rejects_wrong_dimension(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini")
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(200, json=_gemini_payload(1, 1535)), [])
    with pytest.raises(embeddings.EmbeddingProviderError, match="dimension"):
        asyncio.run(embeddings.embed_texts(["test"]))


def test_gemini_embedding_rejects_malformed_response(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini")
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(200, json={"unexpected": []}), [])
    with pytest.raises(embeddings.EmbeddingProviderError, match="malformed"):
        asyncio.run(embeddings.embed_texts(["test"]))


def test_gemini_embedding_http_failure_is_provider_error(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini")
    monkeypatch.setattr(embeddings, "EMBEDDING_MAX_RETRIES", 1)
    async def fake_sleep(_delay):
        return None
    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    requests_seen = []
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(429, json={"error": {"message": "rate limited"}}), requests_seen)
    with pytest.raises(EmbeddingRateLimitError, match="after 2 attempts"):
        asyncio.run(embeddings.embed_texts(["test"]))
    assert len(requests_seen) == 2


def test_openai_embedding_behavior_is_preserved(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "openai", model="text-embedding-test")
    requests_seen = []
    payload = {"data": [
        {"index": 1, "embedding": [0.5] * 768},
        {"index": 0, "embedding": [0.25] * 768},
    ]}
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(200, json=payload), requests_seen)

    vectors = asyncio.run(embeddings.embed_texts(["first", "second"]))

    assert len(vectors) == 2 and vectors[0][0] == 0.25 and vectors[1][0] == 0.5
    assert requests_seen[0].url.path == "/v1beta/embeddings"
    assert requests_seen[0].headers["authorization"] == "Bearer unit-test-key"
    assert __import__("json").loads(requests_seen[0].content) == {
        "model": "text-embedding-test", "input": ["first", "second"]}


@pytest.mark.parametrize("provider,expected_suffix", [
    ("gemini", "/models/gemini-embedding-2:batchEmbedContents"),
    ("openai_compatible", "/embeddings"),
])
def test_embedding_provider_selection(monkeypatch, provider, expected_suffix):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, provider)
    requests_seen = []
    payload_factory = (lambda _: httpx.Response(200, json=_gemini_payload(1))) if provider == "gemini" else (
        lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 768}]}))
    _mock_embedding_client(monkeypatch, payload_factory, requests_seen)
    assert len(asyncio.run(embeddings.embed_texts(["test"]))) == 1
    assert requests_seen[0].url.path.endswith(expected_suffix)


def test_gemini_429_retry_honors_retry_after(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini", batch_size=1)
    monkeypatch.setattr(embeddings, "EMBEDDING_MAX_RETRIES", 3)
    monkeypatch.setattr(embeddings.random, "uniform", lambda _low, _high: 0.25)
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    requests_seen = []

    def response(request):
        if len(requests_seen) <= 3:
            return httpx.Response(429, headers={"Retry-After": str(len(requests_seen) + 1)},
                json={"error": {"status": "RESOURCE_EXHAUSTED"}})
        return httpx.Response(200, json=_gemini_payload(1))

    _mock_embedding_client(monkeypatch, response, requests_seen)
    vectors = asyncio.run(embeddings.embed_texts(["test"]))
    assert len(vectors) == 1 and len(requests_seen) == 4
    assert sleeps == [2.0, 3.0, 4.0]


def test_successful_gemini_requests_are_paced(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini", batch_size=1)
    monkeypatch.setattr(embeddings, "GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS", 1.0)
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    requests_seen = []
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(200, json=_gemini_payload(1)), requests_seen)
    vectors = asyncio.run(embeddings.embed_texts(["one", "two", "three"]))
    assert len(vectors) == len(requests_seen) == 3
    assert sleeps == [1.0, 1.0]


def test_gemini_retry_backoff_is_exponential_and_bounded(monkeypatch):
    import app.services.embeddings as embeddings
    _configure_embedding_provider(monkeypatch, "gemini", batch_size=1)
    monkeypatch.setattr(embeddings, "EMBEDDING_MAX_RETRIES", 2)
    monkeypatch.setattr(embeddings, "EMBEDDING_RETRY_BASE_SECONDS", 1.0)
    monkeypatch.setattr(embeddings, "EMBEDDING_RETRY_MAX_SECONDS", 2.0)
    monkeypatch.setattr(embeddings.random, "uniform", lambda _low, high: high)
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    requests_seen = []
    _mock_embedding_client(monkeypatch, lambda _: httpx.Response(429, json={"error": {}}), requests_seen)
    with pytest.raises(EmbeddingRateLimitError, match="after 3 attempts"):
        asyncio.run(embeddings.embed_texts(["test"]))
    assert len(requests_seen) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_after_parser_accepts_seconds_and_http_date(monkeypatch):
    import app.services.embeddings as embeddings
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    assert embeddings._retry_after_seconds("3") == 3.0
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5), usegmt=True)
    assert 0 < embeddings._retry_after_seconds(future) <= 5
    assert embeddings._retry_after_seconds("not-a-date") is None


def test_resumable_indexing_skips_a_valid_existing_vector():
    from app.services.search_index import _has_valid_vector, _needs_embedding, _refresh_indexed_flag
    chunk = SimpleNamespace(embedding=[0.125] * 768, embedding_model="gemini-embedding-2",
        embedding_dimension=768, is_indexed=True)
    assert _has_valid_vector(chunk, "gemini-embedding-2", 768)
    assert not _needs_embedding(chunk, "gemini-embedding-2", 768)
    assert _needs_embedding(chunk, "different-model", 768)
    assert _needs_embedding(SimpleNamespace(embedding=None), "gemini-embedding-2", 768)
    unembedded = SimpleNamespace(embedding=None, embedding_model=None, embedding_dimension=None, is_indexed=True)
    assert not _refresh_indexed_flag(unembedded, True, "gemini-embedding-2", 768)


def test_idempotent_chunk_rerun_preserves_same_text_vector():
    from app.services.search_index import _refresh_indexed_flag
    vector = [0.125] * 768
    chunk = SimpleNamespace(embedding=vector, embedding_model="gemini-embedding-2",
        embedding_dimension=768, is_indexed=True)
    assert _refresh_indexed_flag(chunk, True, "gemini-embedding-2", 768)
    assert chunk.embedding is vector
    assert not _refresh_indexed_flag(chunk, False, "gemini-embedding-2", 768)
    assert chunk.embedding is vector


def test_partial_indexing_failure_preserves_committed_vector_fields():
    from app.services.search_index import _mark_job_failed
    vector = [0.125] * 768
    chunk = SimpleNamespace(embedding=vector, embedding_model="gemini-embedding-2",
        embedding_dimension=768, is_indexed=True)
    job = SimpleNamespace(status="PROCESSING", vector_count=0, error_message=None, completed_at=None)
    _mark_job_failed(job, EmbeddingRateLimitError("HTTP 429 after 3 attempts"), vector_count=96)
    assert job.status == "FAILED" and job.vector_count == 96
    assert "HTTP 429" in job.error_message
    assert chunk.embedding is vector and chunk.embedding_model == "gemini-embedding-2"
    assert chunk.embedding_dimension == 768 and chunk.is_indexed is True


def _page_fixture():
    page_id, version_id, doc_id, content_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cell_a, cell_b = uuid.uuid4(), uuid.uuid4()
    page = SimpleNamespace(id=page_id, page_number=7, page_metadata={"heading": "Section A"}, text="Fallback page text",
        contents=[SimpleNamespace(id=content_id, created_at=None, content_type="paragraph", text="One source paragraph.\nAnother sentence.",
            extraction_metadata={"heading": "Verified section"}, confidence=None, bounding_box=None)],
        tables=[SimpleNamespace(id=uuid.uuid4(), table_order=1, cells=[
            SimpleNamespace(id=cell_a, row_index=0, column_index=0, raw_value="Year", normalized_value=None),
            SimpleNamespace(id=cell_b, row_index=0, column_index=1, raw_value="Output", normalized_value=None),
            SimpleNamespace(id=uuid.uuid4(), row_index=1, column_index=0, raw_value="2024", normalized_value=None),
            SimpleNamespace(id=uuid.uuid4(), row_index=1, column_index=1, raw_value="10 t", normalized_value=None)])])
    page.tables[0].cells.sort(key=lambda cell: (cell.row_index, cell.column_index))
    return SimpleNamespace(id=doc_id), SimpleNamespace(id=version_id), page


def test_chunk_ids_and_provenance_are_stable():
    document, version, page = _page_fixture()
    first, second = chunk_page(document, version, page), chunk_page(document, version, page)
    assert [item.id for item in first] == [item.id for item in second]
    assert all(item.document_id == document.id and item.document_version_id == version.id for item in first)
    assert all(item.page_id == page.id and item.page_number == 7 for item in first)
    table = next(item for item in first if item.content_type == "table_row")
    assert "Year: 2024" in table.text and "Output: 10 t" in table.text
    assert table.metadata["cell_ids"]


def test_chunk_split_respects_size_and_overlap():
    text = ("A paragraph with useful evidence. " * 250)
    parts = _split(text, max_chars=500, overlap=50)
    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert all(part.strip() for part in parts)


def test_text_spans_are_grouped_by_source_block_without_losing_unit_ids():
    document, version, page = _page_fixture()
    spans = [SimpleNamespace(id=uuid.uuid4(), created_at=None, content_type="text_span", text=f"Evidence {i} ",
        extraction_metadata={"block_index": 0, "line_index": i // 4, "span_index": i % 4}, confidence=None,
        bounding_box={"x0": i, "y0": i}) for i in range(20)]
    page.contents = spans
    page.tables = []
    chunks = chunk_page(document, version, page)
    assert len(chunks) < len(spans)
    referenced = {source_id for item in chunks for source_id in item.metadata["source_unit_ids"]}
    assert referenced == {str(span.id) for span in spans}
    assert all(item.source_unit_type == "TEXT_BLOCK" for item in chunks)


def test_embedding_configuration_fails_explicitly_when_unconfigured(monkeypatch):
    import app.services.embeddings as embeddings
    monkeypatch.setattr(embeddings, "EMBEDDING_PROVIDER", "")
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "")
    monkeypatch.setattr(embeddings, "EMBEDDING_API_KEY", "")
    assert embedding_configuration()["configured"] is False
    with pytest.raises(EmbeddingUnavailable, match="must be configured"):
        asyncio.run(embed_texts(["a query from a test"] ))


def test_llm_configuration_fails_explicitly_when_unconfigured(monkeypatch):
    import app.services.llm as llm
    monkeypatch.setattr(llm, "LLM_PROVIDER", "")
    monkeypatch.setattr(llm, "LLM_MODEL", "")
    monkeypatch.setattr(llm, "LLM_API_KEY", "")
    assert llm_configuration()["configured"] is False
    with pytest.raises(LLMUnavailable, match="must be configured"):
        asyncio.run(generate_answer("system", "test"))


def test_search_and_rag_require_authentication():
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        response = client.post("/api/v1/search/semantic", json={"query": "test"})
        rag = client.post("/api/v1/rag/query", json={"question": "test"})
        assert response.status_code == 401
        assert rag.status_code == 401
    app.dependency_overrides.clear()
