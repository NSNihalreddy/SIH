import asyncio
import builtins
import sys
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.services import embeddings
from app.services.search_index import _has_valid_vector, _needs_embedding


def test_local_document_and_query_embedding_use_retrieval_prefixes_without_api_key(monkeypatch):
    observed = []

    class FakeModel:
        def get_sentence_embedding_dimension(self): return 768
        def encode(self, inputs, **kwargs):
            observed.append((inputs, kwargs))
            return [[0.125] * 768 for _ in inputs]

    monkeypatch.setattr(embeddings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setattr(embeddings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(embeddings, "VECTOR_EMBEDDING_DIMENSION", 768)
    monkeypatch.setattr(embeddings, "_local_sentence_transformer", lambda: FakeModel())

    document_vectors = asyncio.run(embeddings.embed_texts(["ore assay source text"]))
    query_vector = asyncio.run(embeddings.embed_query("where is the assay result?"))
    assert len(document_vectors[0]) == len(query_vector) == 768
    assert observed[0][0] == ["passage: ore assay source text"]
    assert observed[1][0] == ["query: where is the assay result?"]
    assert observed[0][1]["normalize_embeddings"] is True
    assert embeddings.embedding_configuration()["configured"] is True


def test_local_embedding_rejects_unexpected_native_dimension(monkeypatch):
    class WrongDimension:
        def get_sentence_embedding_dimension(self): return 384
        def encode(self, *_args, **_kwargs): raise AssertionError("must reject before inference")
    monkeypatch.setattr(embeddings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setattr(embeddings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(embeddings, "VECTOR_EMBEDDING_DIMENSION", 768)
    monkeypatch.setattr(embeddings, "_local_sentence_transformer", lambda: WrongDimension())
    with pytest.raises(embeddings.EmbeddingProviderError, match="Local model dimension"):
        asyncio.run(embeddings.embed_texts(["passage"]))


def test_local_vectors_are_not_compatible_with_existing_gemini_vectors():
    old = SimpleNamespace(embedding=[0.1] * 768, embedding_model="gemini-embedding-2",
        embedding_dimension=768, is_indexed=True)
    assert _has_valid_vector(old, "gemini-embedding-2", 768)
    assert _needs_embedding(old, "intfloat/multilingual-e5-base", 768)

    local = SimpleNamespace(embedding=[0.2] * 768, embedding_model="intfloat/multilingual-e5-base",
        embedding_dimension=768, is_indexed=True)
    assert _has_valid_vector(local, "intfloat/multilingual-e5-base", 768)
    assert not _needs_embedding(local, "intfloat/multilingual-e5-base", 768)


def test_local_onnx_query_embedding_uses_query_prefix_and_never_imports_torch(monkeypatch):
    observed = []

    class FakeTokenizer:
        def encode_batch(self, texts, add_special_tokens=True):
            observed.append((texts, add_special_tokens))
            return [SimpleNamespace(ids=[0, 1, 2], attention_mask=[1, 1, 1]) for _ in texts]

    class FakeSession:
        def get_outputs(self): return [SimpleNamespace(name="last_hidden_state")]
        def run(self, names, inputs):
            observed.append((names, inputs["input_ids"].shape, inputs["attention_mask"].shape))
            return [np.ones((inputs["input_ids"].shape[0], 3, 768), dtype=np.float32)]

    monkeypatch.setattr(embeddings, "_local_onnx_runtime", lambda: (FakeSession(), FakeTokenizer()))
    original_import = builtins.__import__

    def forbid_torch(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch.") or name == "sentence_transformers":
            raise AssertionError(f"forbidden import attempted: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid_torch)
    monkeypatch.setattr(embeddings, "VECTOR_EMBEDDING_DIMENSION", 768)
    vectors = embeddings._local_onnx_embed_sync(["coal categories"], "query")

    assert observed[0] == (["query: coal categories"], True)
    assert len(vectors) == 1 and len(vectors[0]) == 768
    assert all(np.isfinite(vectors[0]))
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0, abs=1e-6)
    assert "torch" not in sys.modules


def test_local_onnx_document_embedding_uses_passage_prefix(monkeypatch):
    captured = []

    class FakeTokenizer:
        def encode_batch(self, texts, add_special_tokens=True):
            captured.extend(texts)
            return [SimpleNamespace(ids=[1], attention_mask=[1]) for _ in texts]

    class FakeSession:
        def get_outputs(self): return [SimpleNamespace(name="last_hidden_state")]
        def run(self, _names, inputs):
            return [np.ones((len(inputs["input_ids"]), 1, 768), dtype=np.float32)]

    monkeypatch.setattr(embeddings, "_local_onnx_runtime", lambda: (FakeSession(), FakeTokenizer()))
    assert len(embeddings._local_onnx_embed_sync(["source evidence"], "document")[0]) == 768
    assert captured == ["passage: source evidence"]


def test_local_onnx_wrong_output_dimension_fails_closed(monkeypatch):
    class FakeTokenizer:
        def encode_batch(self, texts, add_special_tokens=True):
            return [SimpleNamespace(ids=[1], attention_mask=[1]) for _ in texts]

    class FakeSession:
        def get_outputs(self): return [SimpleNamespace(name="last_hidden_state")]
        def run(self, _names, inputs):
            return [np.ones((len(inputs["input_ids"]), 1, 767), dtype=np.float32)]

    monkeypatch.setattr(embeddings, "_local_onnx_runtime", lambda: (FakeSession(), FakeTokenizer()))
    with pytest.raises(embeddings.EmbeddingProviderError, match="output shape"):
        embeddings._local_onnx_embed_sync(["query"], "query")


def test_local_onnx_provider_selection_works_without_api_key(monkeypatch):
    monkeypatch.setattr(embeddings, "EMBEDDING_PROVIDER", "local_onnx")
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setattr(embeddings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(embeddings, "VECTOR_EMBEDDING_DIMENSION", 768)
    monkeypatch.setattr(embeddings, "_local_onnx_embed_sync", lambda texts, input_type: [[0.0] * 768 for _ in texts])

    vectors = asyncio.run(embeddings.embed_query("coal categories"))

    assert len(vectors) == 768
    assert embeddings.embedding_configuration() == {
        "provider": "local_onnx", "model": "intfloat/multilingual-e5-base",
        "dimension": 768, "configured": True,
    }


def test_local_onnx_model_hash_is_verified_before_loading(monkeypatch, tmp_path):
    import hashlib
    from pathlib import Path
    import onnxruntime
    import tokenizers

    model = tmp_path / "model.onnx"
    model.write_bytes(b"official model placeholder for hash-validation unit test")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(embeddings, "LOCAL_ONNX_MODEL_DIR", tmp_path)
    monkeypatch.setattr(embeddings, "LOCAL_ONNX_MODEL_SHA256", hashlib.sha256(model.read_bytes()).hexdigest())

    class FakeTokenizer:
        @classmethod
        def from_file(cls, _path): return cls()
        def enable_truncation(self, **_kwargs): pass
        def token_to_id(self, _token): return 1
        def enable_padding(self, **_kwargs): pass

    class FakeSession:
        def __init__(self, _path, providers):
            assert providers == ["CPUExecutionProvider"]
        def get_providers(self): return ["CPUExecutionProvider"]
        def get_inputs(self): return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    monkeypatch.setattr(tokenizers.Tokenizer, "from_file", FakeTokenizer.from_file)
    embeddings._local_onnx_runtime.cache_clear()
    try:
        session, tokenizer = embeddings._local_onnx_runtime()
        assert isinstance(session, FakeSession)
        assert isinstance(tokenizer, FakeTokenizer)
        model.write_bytes(b"modified model")
        embeddings._local_onnx_runtime.cache_clear()
        with pytest.raises(embeddings.EmbeddingUnavailable, match="SHA-256"):
            embeddings._local_onnx_runtime()
    finally:
        embeddings._local_onnx_runtime.cache_clear()


class _MaintenanceSession:
    def __init__(self): self.scalars = iter([None, 708, 708]); self.updated = None; self.added = []; self.commits = 0
    async def get(self, _model, _id): return SimpleNamespace(id=_id)
    async def scalar(self, _query): return next(self.scalars)
    async def execute(self, query): self.updated = query
    def add(self, value): self.added.append(value)
    async def commit(self): self.commits += 1


def test_vector_clear_is_admin_confirmed_and_scoped_to_embedding_fields():
    version_id = uuid.uuid4()
    session = _MaintenanceSession()
    actor = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    async def current_user(): return actor
    async def db_session(): yield session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/v1/search/index/versions/{version_id}/clear-vectors", json={
                "confirm_version_id": str(version_id), "expected_vector_count": 708,
                "expected_embedding_model": "gemini-embedding-2", "expected_embedding_dimension": 1536,
            })
        assert response.status_code == 200
        assert response.json()["cleared_vector_count"] == 708
        sql = str(session.updated)
        assert "UPDATE document_chunks" in sql
        assert "document_version_id" in sql
        assert len(session.added) == 1 and session.added[0].response_status == "VECTOR_REBUILD_PREPARED"
        assert session.commits == 1
    finally:
        app.dependency_overrides.clear()
