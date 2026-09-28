import asyncio
import hashlib
from types import SimpleNamespace
import uuid

import pytest

from app.services import gemini_batch, search_index
from app.services.embeddings import EmbeddingProviderError


DIMENSION = 768


class FakeBatchAPI:
    def __init__(self, created=None, polled=None):
        self.created = created or {"name": "batches/test-123"}
        self.polled = polled
        self.submissions = []
        self.get_calls = []

    def create_embeddings(self, **kwargs):
        self.submissions.append(kwargs)
        return self.created

    def get(self, *, name):
        self.get_calls.append(name)
        return self.polled


def _fake_client(api):
    return SimpleNamespace(batches=api)


def test_mock_batch_submission_requests_dimension_and_returns_external_id(monkeypatch):
    api = FakeBatchAPI()
    monkeypatch.setattr(gemini_batch, "_default_client", lambda: _fake_client(api))
    result = asyncio.run(gemini_batch.create_embedding_batch(["one", "two"]))
    assert result == "batches/test-123"
    args = api.submissions[0]
    assert args["model"] == gemini_batch.EMBEDDING_MODEL
    source = args["src"]["inlined_requests"]
    assert [item["parts"][0]["text"] for item in source["contents"]] == ["one", "two"]
    assert source["config"]["output_dimensionality"] == DIMENSION


def test_submission_provider_failure_is_diagnostic(monkeypatch):
    api = FakeBatchAPI()
    def fail(**_kwargs): raise RuntimeError("mock transport down")
    api.create_embeddings = fail
    monkeypatch.setattr(gemini_batch, "_default_client", lambda: _fake_client(api))
    with pytest.raises(EmbeddingProviderError, match="submission failed.*mock transport down"):
        asyncio.run(gemini_batch.create_embedding_batch(["text"]))


def test_poll_mock_success_parses_and_validates_batch_vectors(monkeypatch):
    api = FakeBatchAPI(polled={"state": "JOB_STATE_SUCCEEDED", "dest": {
        "inlined_embed_content_responses": [
            {"response": {"embedding": {"values": [0.25] * DIMENSION}}},
            {"response": {"embedding": {"values": [0.5] * DIMENSION}}},
        ]}})
    monkeypatch.setattr(gemini_batch, "_default_client", lambda: _fake_client(api))
    result = asyncio.run(gemini_batch.poll_embedding_batch("batches/x", 2))
    assert result["state"] == "SUCCEEDED"
    assert len(result["vectors"]) == 2 and len(result["vectors"][0]) == DIMENSION
    assert api.get_calls == ["batches/x"]


@pytest.mark.parametrize("state, expected", [
    ("JOB_STATE_PENDING", "RUNNING"), ("JOB_STATE_RUNNING", "RUNNING"),
    ("JOB_STATE_CANCELLED", "CANCELLED"), ("JOB_STATE_EXPIRED", "EXPIRED"),
])
def test_poll_batch_states(state, expected, monkeypatch):
    api = FakeBatchAPI(polled={"state": state})
    monkeypatch.setattr(gemini_batch, "_default_client", lambda: _fake_client(api))
    result = asyncio.run(gemini_batch.poll_embedding_batch("batches/x", 1))
    assert result["state"] == expected


def test_poll_failed_batch_reports_provider_diagnostic(monkeypatch):
    api = FakeBatchAPI(polled={"state": "JOB_STATE_FAILED", "error": {"message": "quota exhausted"}})
    monkeypatch.setattr(gemini_batch, "_default_client", lambda: _fake_client(api))
    result = asyncio.run(gemini_batch.poll_embedding_batch("batches/x", 1))
    assert result["state"] == "FAILED" and "quota exhausted" in result["error"]


def test_poll_rejects_bad_dimensions_nonfinite_and_malformed_results(monkeypatch):
    malformed_jobs = [
        {"state": "SUCCEEDED", "dest": {"inlined_embed_content_responses": [
            {"response": {"embedding": {"values": [1.0] * (DIMENSION - 1)}}}]}},
        {"state": "SUCCEEDED", "dest": {"inlined_embed_content_responses": [
            {"response": {"embedding": {"values": [float("nan")] + [1.0] * (DIMENSION - 1)}}}]}},
        {"state": "SUCCEEDED", "dest": {"inlined_embed_content_responses": []}},
    ]
    for batch in malformed_jobs:
        api = FakeBatchAPI(polled=batch)
        monkeypatch.setattr(gemini_batch, "_default_client", lambda api=api: _fake_client(api))
        result = asyncio.run(gemini_batch.poll_embedding_batch("batches/x", 1))
        assert result["state"] == "FAILED"
        assert result["error"]


class _Scalars:
    def __init__(self, values): self.values = values
    def all(self): return self.values


class _Result:
    def __init__(self, values): self.values = values
    def scalars(self): return _Scalars(self.values)


class _FakeSession:
    def __init__(self, chunks):
        self.chunks = {chunk.id: chunk for chunk in chunks}
        self.commits = 0
    async def execute(self, _query): return _Result(list(self.chunks.values()))
    async def get(self, _model, chunk_id): return self.chunks.get(chunk_id)
    async def scalar(self, _query):
        return sum(chunk.is_indexed and chunk.embedding is not None for chunk in self.chunks.values())
    async def flush(self): pass
    async def commit(self): self.commits += 1


def _chunk(text, vector=None, indexed=False):
    return SimpleNamespace(id=uuid.uuid4(), text=text, embedding=vector,
        embedding_model="gemini-embedding-2" if vector else None,
        embedding_dimension=DIMENSION if vector else None, is_indexed=indexed)


def test_batch_resume_persists_valid_results_and_skips_unchanged_existing_vectors(monkeypatch):
    existing = _chunk("already embedded", [0.1] * DIMENSION, indexed=True)
    pending = _chunk("needs embedding")
    session = _FakeSession([existing, pending])
    calls = []

    async def create(texts):
        calls.extend(texts)
        return "batches/new"

    monkeypatch.setattr(search_index.gemini_batch, "create_embedding_batch", create)
    job = SimpleNamespace(id=uuid.uuid4(), provider_metadata=None, vector_count=1,
        chunk_count=2, status="PROCESSING", completed_at=None)
    out = asyncio.run(search_index._run_gemini_batch_step(session, job, uuid.uuid4(), "gemini-embedding-2", DIMENSION))
    assert out["status"] == "PROCESSING"
    assert calls == ["needs embedding"]
    assert job.provider_metadata["batches"][0]["chunk_ids"] == [str(pending.id)]


def test_completed_indexing_rerun_reuses_all_valid_vectors(monkeypatch):
    existing = _chunk("already embedded", [0.1] * DIMENSION, indexed=True)
    session = _FakeSession([existing])
    async def should_not_submit(_texts): raise AssertionError("valid vector was resubmitted")
    monkeypatch.setattr(search_index.gemini_batch, "create_embedding_batch", should_not_submit)
    job = SimpleNamespace(id=uuid.uuid4(), provider_metadata=None, vector_count=1,
        chunk_count=1, status="PROCESSING", completed_at=None)
    result = asyncio.run(search_index._run_gemini_batch_step(session, job, uuid.uuid4(), "gemini-embedding-2", DIMENSION))
    assert result["status"] == "COMPLETED"
    assert result["vector_count"] == 1


def test_completed_batch_partial_source_change_persists_other_vector_then_resumes(monkeypatch):
    keep = _chunk("unchanged")
    changed = _chunk("edited while batch ran")
    job_meta = {"provider": "gemini_batch_v1", "batches": [{
        "name": "batches/partial", "state": "SUBMITTED",
        "chunk_ids": [str(keep.id), str(changed.id)],
        "text_sha256": [hashlib.sha256(b"unchanged").hexdigest(), hashlib.sha256(b"old source").hexdigest()],
    }]}
    session = _FakeSession([keep, changed])
    async def poll(*_args): return {"state": "SUCCEEDED", "vectors": [[0.3] * DIMENSION, [0.4] * DIMENSION]}
    async def create(texts): return "batches/next"
    monkeypatch.setattr(search_index.gemini_batch, "poll_embedding_batch", poll)
    monkeypatch.setattr(search_index.gemini_batch, "create_embedding_batch", create)
    job = SimpleNamespace(id=uuid.uuid4(), provider_metadata=job_meta, vector_count=0,
        chunk_count=2, status="PROCESSING", completed_at=None)
    out = asyncio.run(search_index._run_gemini_batch_step(session, job, uuid.uuid4(), "gemini-embedding-2", DIMENSION))
    assert out["status"] == "PROCESSING"
    assert keep.is_indexed and keep.embedding == [0.3] * DIMENSION
    assert not changed.is_indexed and changed.embedding is None
    assert session.commits >= 2
    assert job.provider_metadata["batches"][0]["name"] == "batches/next"
