import asyncio
import uuid
from typing import get_args, get_type_hints
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.intelligence import compare_documents, extract_document_intelligence, temporal_status
from app.api import routes_intelligence
from app.workers import tasks


def _chunk(text, doc="doc-a", chunk="chunk-a", page="page-a"):
    return {"text": text, "document_id": doc, "document_version_id": "version-" + doc,
        "page_id": page, "page_number": 1, "source_unit_id": chunk,
        "chunk_id": chunk, "evidence_type": "SOURCE_DOCUMENT"}


def test_topic_and_keyword_candidates_come_from_actual_content_with_provenance():
    result = extract_document_intelligence([_chunk("Coal production from underground mining. Coal production.")])
    coal = next(item for item in result["keywords"] if item["term"] == "coal")
    assert coal["frequency"] == 1
    assert coal["token_frequency"] == 2
    assert coal["document_count"] == 1
    assert coal["evidence"][0]["chunk_id"] == "chunk-a"
    assert result["topics"]
    assert result["topics"][0]["evidence"][0]["page_id"] == "page-a"


def test_content_empty_does_not_fabricate_topics():
    result = extract_document_intelligence([])
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["topics"] == []
    assert result["keywords"] == []


def test_frequency_distribution_carries_document_relationships():
    result = extract_document_intelligence([_chunk("coal seam", "a", "c1"), _chunk("coal seam", "b", "c2")])
    topic = next(item for item in result["topics"] if item["name"] == "coal seam")
    assert topic["document_count"] == 2
    assert {item["document_id"] for item in topic["distribution"]} == {"a", "b"}


def test_multidocument_comparison_is_deterministic_and_non_authoritative_for_numbers():
    left = extract_document_intelligence([_chunk("coal production", "a", "c1")])
    right = extract_document_intelligence([_chunk("coal seam", "b", "c2")])
    compared = compare_documents({"a": left, "b": right})
    assert compared["status"] == "COMPLETED"
    assert compared["numerical_analysis_status"] == "INSUFFICIENT_VERIFIED_DATA"
    assert "coal production" in compared["unique_topics"]["a"]


def test_comparison_requires_two_documents():
    assert compare_documents({"a": {"topics": []}})["status"] == "INSUFFICIENT_EVIDENCE"


def test_ingestion_timestamps_are_not_misrepresented_as_temporal_trends():
    assert temporal_status([{"created_at": "2026-01-01"}]) == "INSUFFICIENT_TEMPORAL_DATA"


def test_admin_requeue_preserves_run_id_and_submits_only_once(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), scope="CORPUS", status="PENDING", error_message="old failure")
    actor = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    class Session:
        def __init__(self): self.lookups = 0; self.added = []; self.commits = 0
        async def execute(self, *_args, **_kwargs): return None
        async def scalar(self, *_args, **_kwargs):
            self.lookups += 1
            return {1: run, 2: None, 3: run}.get(self.lookups)
        def add(self, value): self.added.append(value)
        async def flush(self): pass
        async def commit(self): self.commits += 1
        async def rollback(self): pass
    session = Session()
    submissions = []
    monkeypatch.setattr(routes_intelligence.build_intelligence_task, "apply_async",
        lambda **kwargs: submissions.append(kwargs))

    first = asyncio.run(routes_intelligence.requeue_run(run.id, actor, session))
    second = asyncio.run(routes_intelligence.requeue_run(run.id, actor, session))
    assert first["run_id"] == run.id and first["submitted"] is True
    assert first["status"] == "QUEUED"
    assert second["submitted"] is False and second["run_id"] == run.id
    assert run.error_message is None
    assert len(submissions) == 1
    assert submissions[0]["args"] == [str(run.id)]
    assert submissions[0]["task_id"] == f"intelligence-run-{run.id}"
    dependency = get_args(get_type_hints(routes_intelligence.requeue_run, include_extras=True)["actor"])[1].dependency
    assert any(cell.cell_contents == ("ADMIN",) for cell in dependency.__closure__ or ())


def test_admin_requeue_refuses_when_another_run_is_active(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), scope="CORPUS", status="PENDING", error_message=None)
    other = SimpleNamespace(id=uuid.uuid4(), scope="CORPUS", status="PROCESSING")
    actor = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    class Session:
        def __init__(self): self.lookups = 0
        async def execute(self, *_args, **_kwargs): return None
        async def scalar(self, *_args, **_kwargs):
            self.lookups += 1
            return run if self.lookups == 1 else other
    submissions = []
    monkeypatch.setattr(routes_intelligence.build_intelligence_task, "apply_async",
        lambda **kwargs: submissions.append(kwargs))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_intelligence.requeue_run(run.id, actor, Session()))
    assert exc.value.status_code == 409
    assert run.status == "PENDING"
    assert submissions == []


def test_requeue_has_an_internal_admin_guard():
    actor = SimpleNamespace(id=uuid.uuid4(), role="ANALYST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_intelligence.requeue_run(uuid.uuid4(), actor, None))
    assert exc.value.status_code == 403


def test_task_claim_is_single_winner_for_duplicate_delivery():
    class Result:
        def __init__(self, row): self.row = row
        def scalar_one_or_none(self): return self.row
    class Session:
        def __init__(self, row): self.row = row; self.commits = 0
        def execute(self, *_args, **_kwargs): return Result(self.row)
        def commit(self): self.commits += 1
    queued = SimpleNamespace(status="QUEUED", started_at=None)
    first_session = Session(queued)
    assert tasks._claim_intelligence_run(first_session, str(uuid.uuid4())) is queued
    assert queued.status == "PROCESSING" and first_session.commits == 1
    duplicate_session = Session(queued)
    assert tasks._claim_intelligence_run(duplicate_session, str(uuid.uuid4())) is None
    assert duplicate_session.commits == 0
