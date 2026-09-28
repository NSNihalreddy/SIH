import asyncio
import uuid
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.rag import SearchAudit
from app.services import evidence as evidence_service
from app.services import rag as rag_service
from app.services import search as search_service
from app.services.embeddings import EmbeddingUnavailable
from app.services.llm import LLMProviderError


def _search_row(*, evidence_type="SOURCE_DOCUMENT", score=0.72):
    chunk_id, document_id, version_id, page_id, source_id = [uuid.uuid4() for _ in range(5)]
    chunk = SimpleNamespace(id=chunk_id, text="The source records a seam observation.",
        document_id=document_id, document_version_id=version_id,
        source_unit_type="TEXT_BLOCK", source_unit_id=source_id,
        source_metadata={"page_number": 12, "source_unit_ids": [str(source_id)]},
        content_type="text_span", evidence_type=evidence_type)
    document = SimpleNamespace(id=document_id, original_filename="real-report.pdf", sha256_checksum="doc-checksum")
    version = SimpleNamespace(id=version_id, sha256_checksum="version-checksum")
    page = SimpleNamespace(id=page_id, page_number=12)
    return (chunk, document, version, page, 0.8, 0.6, score)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _SearchSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Rows(self.rows)


def test_semantic_search_uses_query_embedding_and_returns_traceable_pgvector_result(monkeypatch):
    calls = []

    async def fake_embed(query):
        calls.append(query)
        return [0.01] * 768

    monkeypatch.setattr(search_service, "embed_query", fake_embed)
    session = _SearchSession([_search_row()])
    items = asyncio.run(search_service.search_chunks(session, "seam observation", mode="semantic",
        filters={"content_type": "text_span", "document_id": str(uuid.uuid4())}, top_k=3))

    compiled = str(session.statement.compile(dialect=postgresql.dialect()))
    assert calls == ["seam observation"]
    assert "<=>" in compiled and "document_chunks.is_indexed IS true" in compiled
    assert "content_type" in compiled and "documents.id" in compiled
    item = items[0]
    assert item["document_name"] == "real-report.pdf"
    assert item["page_number"] == 12 and item["page_id"]
    assert item["source_unit_id"] and item["document_version_id"]
    assert item["provenance"]["chunk_id"] == item["result_id"]
    assert item["provenance"]["source_metadata"]["source_unit_ids"]


def test_hybrid_search_combines_semantic_and_lexical_scores_with_traceability(monkeypatch):
    async def fake_embed(_query):
        return [0.01] * 768

    monkeypatch.setattr(search_service, "embed_query", fake_embed)
    session = _SearchSession([_search_row(score=0.73)])
    items = asyncio.run(search_service.search_chunks(session, "seam observation", mode="hybrid", top_k=2))
    compiled = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "<=>" in compiled and "document_chunks.is_indexed IS true" in compiled
    assert "to_tsvector" in compiled and "plainto_tsquery" in compiled and "ts_rank_cd" in compiled
    assert "document_chunks.id ASC" in compiled and "DESC" in compiled
    assert "evidence_type" in compiled
    assert items[0]["semantic_score"] == 0.8 and items[0]["lexical_score"] == 0.6
    assert items[0]["score"] == 0.73 and items[0]["page_number"] == 12
    assert items[0]["evidence_type"] == "SOURCE_DOCUMENT"


def test_lexical_search_needs_no_embedding_and_empty_results_are_empty(monkeypatch):
    async def should_not_embed(_query):
        raise AssertionError("lexical search must not call the embedding provider")

    monkeypatch.setattr(search_service, "embed_query", should_not_embed)
    session = _SearchSession([])
    assert asyncio.run(search_service.search_chunks(session, "text phrase", mode="lexical")) == []
    compiled = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "to_tsvector" in compiled and "document_chunks.is_indexed IS true" not in compiled


@pytest.mark.parametrize("query,top_k,mode", [("x" * 1001, 5, "semantic"), ("query", 0, "hybrid"), ("query", 5, "invalid")])
def test_search_rejects_invalid_query_parameters(query, top_k, mode):
    with pytest.raises(ValueError):
        asyncio.run(search_service.search_chunks(_SearchSession([]), query, top_k=top_k, mode=mode))


def _evidence(row, evidence_type=None):
    kind = evidence_type or row["evidence_type"]
    return {"result_id": row["result_id"], "score": row["score"], "semantic_score": row["semantic_score"],
        "lexical_score": row["lexical_score"], "text": row["text"],
        "document_id": row["document_id"], "document_version_id": row["document_version_id"],
        "page_id": row["page_id"], "page_number": row["page_number"],
        "source_unit_id": row["source_unit_id"], "source_unit_type": row["source_unit_type"],
        "document_name": row["document_name"], "content_type": row["content_type"],
        "evidence_type": kind, "provenance": {"chunk_id": row["result_id"]},
        "context": "Page context from the stored source.", "exact_source_text": row["text"]}


def _search_item(row):
    chunk, document, version, page, semantic, lexical, score = row
    return {"result_id": str(chunk.id), "score": score, "semantic_score": semantic,
        "lexical_score": lexical, "text": chunk.text,
        "document_id": str(document.id), "document_version_id": str(version.id),
        "page_id": str(page.id), "page_number": page.page_number,
        "source_unit_id": str(chunk.source_unit_id), "source_unit_type": chunk.source_unit_type,
        "document_name": document.original_filename, "content_type": chunk.content_type,
        "evidence_type": chunk.evidence_type, "provenance": {"chunk_id": str(chunk.id)}, "chunk": chunk}


def test_evidence_retrieval_preserves_exact_chunk_and_surrounding_page_context():
    page_id, chunk_id, unit_id, table_id = [uuid.uuid4() for _ in range(4)]
    page = SimpleNamespace(id=page_id, text="Full page text from the source.")
    neighbor = SimpleNamespace(id=uuid.uuid4(), text="Nearby extracted source text.")
    cell_a = SimpleNamespace(column_index=0, normalized_value="Heading", raw_value=None)
    cell_b = SimpleNamespace(column_index=1, normalized_value=None, raw_value="Value")

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        async def get(self, model, key):
            assert model.__name__ == "DocumentPage" and key == page_id
            return page

        async def execute(self, _statement):
            if len(executed) == 1:
                return Result([neighbor])
            return Result([cell_a, cell_b])

    executed = []
    session = Session()
    original_execute = session.execute

    async def record_execute(statement):
        executed.append(statement)
        return await original_execute(statement)

    session.execute = record_execute
    chunk = SimpleNamespace(id=chunk_id, text="Exact table cell source.", document_page_id=page_id,
        source_unit_id=unit_id, source_metadata={
        "row_index": 2, "table_id": str(table_id)})
    result = {"chunk": chunk, "result_id": str(chunk_id), "score": 0.8,
        "semantic_score": 0.7, "lexical_score": 0.5, "text": "Exact table cell source.",
        "document_id": str(uuid.uuid4()), "document_version_id": str(uuid.uuid4()),
        "page_id": str(page_id), "page_number": 12, "source_unit_id": str(unit_id),
        "source_unit_type": "TABLE_ROW", "document_name": "source.pdf", "content_type": "table_row",
        "evidence_type": "SOURCE_DOCUMENT", "provenance": {"chunk_id": str(chunk_id)}}
    evidence = asyncio.run(evidence_service.retrieve_evidence(session, result))
    assert evidence["exact_source_text"] == "Exact table cell source."
    assert "Full page text from the source." in evidence["context"]
    assert "Nearby extracted source text." in evidence["context"]
    assert "Table row context: Heading | Value" in evidence["context"]
    assert evidence["page_id"] == str(page_id) and evidence["source_unit_id"] == str(unit_id)


def test_rag_context_answer_citations_and_citation_validation(monkeypatch):
    source = _search_item(_search_row())

    async def fake_search(_session, _query, *, top_k, filters, mode):
        return [source]

    async def fake_evidence(_session, row):
        return _evidence(row)

    prompts = []

    async def fake_generate(system, user):
        prompts.append((system, user))
        return "The cited document records a seam observation [E1]."

    monkeypatch.setattr(rag_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service, "retrieve_evidence", fake_evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": True, "model": "mock-model", "provider": "mock"})
    monkeypatch.setattr(rag_service, "generate_answer", fake_generate)
    result = asyncio.run(rag_service.answer_question(None, "Summarize source page 12"))
    assert result["status"] == "ANSWERED"
    assert result["citations"][0]["chunk_id"] == source["result_id"]
    assert result["citations"][0]["page_number"] == 12
    assert result["citations"][0]["source_unit_id"] == source["source_unit_id"]
    assert "seam observation" in prompts[0][1]
    assert "answer only from the supplied evidence" in prompts[0][0].lower()
    assert result["retrieval_metadata"]["scores"][0]["evidence_type"] == "SOURCE_DOCUMENT"

    async def bad_citation(_system, _user):
        return "An unsupported answer [E99]."
    monkeypatch.setattr(rag_service, "generate_answer", bad_citation)
    invalid = asyncio.run(rag_service.answer_question(None, "Summarize source page 12"))
    assert invalid["status"] == "CITATION_VALIDATION_FAILED" and invalid["answer"] is None


def test_rag_citation_identifiers_are_unique_for_multiple_evidence(monkeypatch):
    first = _search_item(_search_row())
    second = _search_item(_search_row())
    second["result_id"] = str(uuid.uuid4())

    async def fake_search(_session, _query, *, top_k, filters, mode):
        return [first, second]

    async def fake_evidence(_session, row):
        return _evidence(row)

    async def fake_generate(_system, _user):
        return "The sources describe observations [E1] and [E2]."

    monkeypatch.setattr(rag_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service, "retrieve_evidence", fake_evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": True, "model": "mock", "provider": "mock"})
    monkeypatch.setattr(rag_service, "generate_answer", fake_generate)
    result = asyncio.run(rag_service.answer_question(None, "Summarize the source documents"))
    assert result["status"] == "ANSWERED"
    assert [item["citation_id"] for item in result["citations"]] == ["E1", "E2"]


def test_rag_empty_evidence_and_llm_unavailable_return_no_invented_answer(monkeypatch):
    async def no_results(*_args, **_kwargs): return []
    async def fail_if_called(*_args, **_kwargs): raise AssertionError("LLM must not run without evidence")
    monkeypatch.setattr(rag_service, "search_chunks", no_results)
    monkeypatch.setattr(rag_service, "generate_answer", fail_if_called)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": True, "model": "mock", "provider": "mock"})
    empty = asyncio.run(rag_service.answer_question(None, "Describe the source"))
    assert empty["status"] == "INSUFFICIENT_EVIDENCE" and not empty["citations"]

    row = _search_item(_search_row())
    async def one_result(*_args, **_kwargs): return [row]
    async def fake_evidence(_session, item): return _evidence(item)
    monkeypatch.setattr(rag_service, "search_chunks", one_result)
    monkeypatch.setattr(rag_service, "retrieve_evidence", fake_evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": False, "model": None, "provider": None})
    unavailable = asyncio.run(rag_service.answer_question(None, "Summarize source page"))
    assert unavailable["status"] == "LLM_UNAVAILABLE" and unavailable["answer"] is None
    assert unavailable["citations"]


def test_llm_adapter_sends_grounded_context_and_parses_mock_provider_response(monkeypatch):
    import app.services.llm as llm_service
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_service, "LLM_MODEL", "mock-chat-model")
    monkeypatch.setattr(llm_service, "LLM_API_KEY", "test-only-key")
    monkeypatch.setattr(llm_service, "LLM_API_BASE_URL", "https://llm.test/v1")
    monkeypatch.setattr(llm_service, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(llm_service, "LLM_MAX_TOKENS", 128)
    real_client = httpx.AsyncClient
    requests_seen = []

    def client_factory(*, timeout):
        async def handler(request):
            requests_seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Grounded answer [E1]."}}]})
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", client_factory)
    answer = asyncio.run(llm_service.generate_answer("Only answer from evidence.", "[E1] Source text"))
    request = requests_seen[0]
    payload = __import__("json").loads(request.content)
    assert answer == "Grounded answer [E1]."
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-only-key"
    assert payload["messages"] == [
        {"role": "system", "content": "Only answer from evidence."},
        {"role": "user", "content": "[E1] Source text"},
    ]
    assert payload["model"] == "mock-chat-model" and payload["max_tokens"] == 128


def test_llm_adapter_reports_mocked_http_failure(monkeypatch):
    import app.services.llm as llm_service
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm_service, "LLM_MODEL", "mock-model")
    monkeypatch.setattr(llm_service, "LLM_API_KEY", "test-only-key")
    real_client = httpx.AsyncClient

    def client_factory(*, timeout):
        async def handler(_request):
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", client_factory)
    with pytest.raises(LLMProviderError, match="request failed"):
        asyncio.run(llm_service.generate_answer("system", "context"))


def test_gemini_llm_adapter_uses_native_generate_content(monkeypatch):
    import app.services.llm as llm_service
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_service, "LLM_MODEL", "mock-gemini-model")
    monkeypatch.setattr(llm_service, "LLM_API_KEY", "test-only-key")
    monkeypatch.setattr(llm_service, "LLM_API_BASE_URL", "https://gemini.test/v1beta/")
    monkeypatch.setattr(llm_service, "LLM_TEMPERATURE", 0.1)
    monkeypatch.setattr(llm_service, "LLM_MAX_TOKENS", 128)
    real_client = httpx.AsyncClient
    requests_seen = []

    def client_factory(*, timeout):
        async def handler(request):
            requests_seen.append(request)
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Grounded "}, {"text": "answer [E1]."}]}}]})
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", client_factory)
    answer = asyncio.run(llm_service.generate_answer("Use supplied evidence.", "[E1] Source text"))
    request = requests_seen[0]
    payload = __import__("json").loads(request.content)
    assert answer == "Grounded answer [E1]."
    assert request.url == "https://gemini.test/v1beta/models/mock-gemini-model:generateContent"
    assert request.headers["x-goog-api-key"] == "test-only-key"
    assert payload["systemInstruction"]["parts"] == [{"text": "Use supplied evidence."}]
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "[E1] Source text"}]}]
    assert payload["generationConfig"] == {"temperature": 0.1, "maxOutputTokens": 128}


@pytest.mark.parametrize("status", [429, 503])
def test_gemini_llm_adapter_reports_http_failures(monkeypatch, status):
    import app.services.llm as llm_service
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_service, "LLM_MODEL", "mock-gemini-model")
    monkeypatch.setattr(llm_service, "LLM_API_KEY", "test-only-key")
    real_client = httpx.AsyncClient

    def client_factory(*, timeout):
        async def handler(_request):
            return httpx.Response(status, json={"error": {"message": "mock failure"}})
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", client_factory)
    with pytest.raises(LLMProviderError, match=f"HTTP {status}"):
        asyncio.run(llm_service.generate_answer("system", "context"))


@pytest.mark.parametrize("body", [{}, {"candidates": []}, {"candidates": [{"content": {"parts": []}}]}])
def test_gemini_llm_adapter_rejects_malformed_or_empty_response(monkeypatch, body):
    import app.services.llm as llm_service
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_service, "LLM_MODEL", "mock-gemini-model")
    monkeypatch.setattr(llm_service, "LLM_API_KEY", "test-only-key")
    real_client = httpx.AsyncClient

    def client_factory(*, timeout):
        async def handler(_request):
            return httpx.Response(200, json=body)
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", client_factory)
    with pytest.raises(LLMProviderError):
        asyncio.run(llm_service.generate_answer("system", "context"))


def test_rag_routes_numeric_questions_to_deterministic_boundary(monkeypatch):
    row = _search_item(_search_row(evidence_type="TRUSTED_CANONICAL_DATA"))
    generated = []
    async def fake_search(*_args, **_kwargs): return [row]
    async def fake_evidence(_session, item): return _evidence(item, "TRUSTED_CANONICAL_DATA")
    async def fake_generate(*_args, **_kwargs): generated.append(True); return "It is 900 tonnes [E1]."
    monkeypatch.setattr(rag_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service, "retrieve_evidence", fake_evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": True, "model": "mock", "provider": "mock"})
    monkeypatch.setattr(rag_service, "generate_answer", fake_generate)
    result = asyncio.run(rag_service.answer_question(None, "What is the total production?"))
    assert result["status"] == "DETERMINISTIC_CALCULATION_REQUIRED"
    assert result["answer"] is None and not generated
    assert result["trust_metadata"]["trusted_data_used"] is True


def test_trusted_data_question_retrieves_canonical_evidence_and_marks_unavailable(monkeypatch):
    source = _search_item(_search_row())
    canonical = _search_item(_search_row(evidence_type="TRUSTED_CANONICAL_DATA", score=0.6))
    calls = []

    async def search(_session, _query, *, top_k, filters, mode):
        calls.append(filters)
        if filters and filters.get("evidence_types") == ["TRUSTED_CANONICAL_DATA"]:
            return [canonical]
        return [source]

    async def evidence(_session, item):
        return _evidence(item)

    monkeypatch.setattr(rag_service, "search_chunks", search)
    monkeypatch.setattr(rag_service, "retrieve_evidence", evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": False, "model": None, "provider": None})
    result = asyncio.run(rag_service.answer_question(None, "What is the recorded production for Mine X?"))
    assert calls[1]["evidence_types"] == ["TRUSTED_CANONICAL_DATA"]
    assert result["evidence"][0]["evidence_type"] == "TRUSTED_CANONICAL_DATA"
    assert result["trust_metadata"]["trusted_data_requested"] is True
    assert result["trust_metadata"]["trusted_data_available"] is True
    assert result["status"] == "LLM_UNAVAILABLE" and result["answer"] is None

    async def source_only(_session, _query, *, top_k, filters, mode):
        return [source]
    monkeypatch.setattr(rag_service, "search_chunks", source_only)
    missing = asyncio.run(rag_service.answer_question(None, "What is the recorded production for Mine X?"))
    assert missing["trust_metadata"]["trusted_data_requested"] is True
    assert missing["trust_metadata"]["trusted_data_available"] is False
    assert missing["evidence"][0]["evidence_type"] == "SOURCE_DOCUMENT"


def test_source_document_questions_do_not_get_routed_as_trusted_queries(monkeypatch):
    source = _search_item(_search_row())
    calls = []

    async def search(_session, _query, *, top_k, filters, mode):
        calls.append(filters)
        return [source]

    async def evidence(_session, item): return _evidence(item)
    monkeypatch.setattr(rag_service, "search_chunks", search)
    monkeypatch.setattr(rag_service, "retrieve_evidence", evidence)
    monkeypatch.setattr(rag_service, "llm_configuration", lambda: {"configured": False, "model": None, "provider": None})
    result = asyncio.run(rag_service.answer_question(None, "What does the source document say about Mine X?"))
    assert calls == [None]
    assert result["trust_metadata"]["trusted_data_requested"] is False


def test_trusted_data_questions_prefer_canonical_but_source_questions_do_not():
    assert rag_service._requires_trusted_data("What is the recorded production for Mine X?")
    assert not rag_service._requires_trusted_data("What does the source document say about Mine X?")


class _AuditSession:
    def __init__(self): self.added = []; self.commits = 0
    def add(self, value): self.added.append(value)
    async def commit(self): self.commits += 1


def _api_overrides(role="VIEWER"):
    actor = SimpleNamespace(id=uuid.uuid4(), role=role)
    session = _AuditSession()
    async def current_user(): return actor
    async def db_session(): yield session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    return session


def test_search_api_enforces_reader_and_indexer_roles_and_writes_audit(monkeypatch):
    import app.api.routes_search as routes
    session = _api_overrides("VIEWER")
    async def no_results(*_args, **_kwargs): return []
    monkeypatch.setattr(routes, "search_chunks", no_results)
    try:
        with TestClient(app) as client:
            lexical = client.post("/api/v1/search/lexical", json={"query": "a source phrase"})
            denied = client.post(f"/api/v1/search/index/versions/{uuid.uuid4()}")
        assert lexical.status_code == 200 and lexical.json()["total"] == 0
        assert denied.status_code == 403
        assert session.commits == 1
        assert len(session.added) == 1 and isinstance(session.added[0], SearchAudit)
        assert session.added[0].response_status == "COMPLETED"
    finally:
        app.dependency_overrides.clear()


def test_semantic_api_reports_missing_embeddings_and_audits_failure(monkeypatch):
    import app.api.routes_search as routes
    session = _api_overrides("ANALYST")
    async def unavailable(*_args, **_kwargs): raise EmbeddingUnavailable("provider unavailable")
    monkeypatch.setattr(routes, "search_chunks", unavailable)
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/search/semantic", json={"query": "a source phrase"})
        assert response.status_code == 503
        assert session.added[0].response_status == "EMBEDDING_UNAVAILABLE"
    finally:
        app.dependency_overrides.clear()


def test_rag_api_audits_unavailable_llm_response(monkeypatch):
    import app.api.routes_search as routes
    session = _api_overrides("VIEWER")
    async def no_llm(*_args, **_kwargs):
        return {"status": "LLM_UNAVAILABLE", "answer": None, "evidence": [], "citations": [],
            "retrieval_metadata": {}, "trust_metadata": {}}
    monkeypatch.setattr(routes, "answer_question", no_llm)
    monkeypatch.setattr(routes, "llm_configuration", lambda: {"provider": None, "model": None, "configured": False})
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/rag/query", json={"question": "Summarize this report"})
        assert response.status_code == 200 and response.json()["status"] == "LLM_UNAVAILABLE"
        assert session.added[0].response_status == "LLM_UNAVAILABLE"
        assert session.commits == 1
    finally:
        app.dependency_overrides.clear()


def test_search_and_rag_api_reject_unauthenticated_requests():
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        semantic = client.post("/api/v1/search/semantic", json={"query": "test"})
        rag = client.post("/api/v1/rag/query", json={"question": "test"})
    assert semantic.status_code == rag.status_code == 401
