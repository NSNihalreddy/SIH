import asyncio
import uuid
from pathlib import Path
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import DATABASE_URL
from app.db.session import get_db
from app.main import app
from app.models.canonical import CanonicalEntity, CanonicalRecord, ExtractionConflict, ExtractionConflictCandidate, MappingProposal, VerificationEvent
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.extraction import ExtractionCandidate
from app.models.intelligence import ExtractedContent, ExtractedTable, ExtractedTableCell
from app.models.identity import AuditLog, User
from app.models.trusted import (TrustedMine, TrustedProject, TrustedBorehole, CoalBlock,
    GeologicalFormation, Seam, TrustedGeologicalMeasurement, TrustedProductionRecord,
    TrustedCoordinate, CanonicalValueHistory, CanonicalDataConflict)
from app.core.auth import hash_password
from app.services.verification import approve_candidate, claim_candidate, classify_candidate, detect_candidate_conflicts, normalize_entity_key, propose_mapping, reject_candidate, resolve_conflict, unresolved_candidate
from app.services.structured_extraction import extract_document_version


@pytest.fixture
def verification_db():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    document_id, version_id, page_id, content_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created_candidates: list[uuid.UUID] = []

    async def setup():
        async with factory() as session:
            session.add(User(id=user_id, username=f"test-{user_id}", email=f"test-{user_id}@example.test", password_hash=hash_password("Test-only-password-123!"), role="VERIFIER"))
            session.add(Document(id=document_id, original_filename="step7-verification-test.pdf", document_type="pdf", mime_type="application/pdf", file_size=0, storage_key=f"tests/{document_id}", status="UPLOADED"))
            session.add(DocumentVersion(id=version_id, document_id=document_id, version_number=1, storage_key=f"tests/{document_id}", file_size=0))
            session.add(DocumentPage(id=page_id, document_version_id=version_id, page_number=1, text="Synthetic source page for isolated workflow tests"))
            session.add(ExtractedContent(id=content_id, document_page_id=page_id, content_type="test", text="Synthetic source content"))
            await session.commit()

    async def create_candidate(*, candidate_type="mine", raw_value="Talcher Coalfield", normalized_value=None, normalized_numeric_value=None, metadata=None, validation=None, source_table_id=None, source_cell_id=None, raw_text=None, unit=None):
        candidate_id = uuid.uuid4()
        created_candidates.append(candidate_id)
        async with factory() as session:
            session.add(ExtractionCandidate(
                id=candidate_id, document_id=document_id, document_version_id=version_id, source_page_id=page_id,
                source_content_id=content_id, source_table_id=source_table_id, source_cell_id=source_cell_id,
                candidate_type=candidate_type, raw_text=raw_text if raw_text is not None else (raw_value or ""),
                raw_value=raw_value, normalized_value=normalized_value if normalized_value is not None else raw_value,
                normalized_numeric_value=normalized_numeric_value,
                unit=unit if unit is not None else ("t" if candidate_type == "production_record" else None), verification_status="PENDING",
                match_status="UNRESOLVED", mapping_status="UNRESOLVED", validation_results=validation or [],
                candidate_metadata=metadata or {},
            ))
            await session.commit()
        return candidate_id

    asyncio.run(setup())
    data = {"engine": engine, "factory": factory, "document_id": document_id, "version_id": version_id, "page_id": page_id, "content_id": content_id, "user_id": user_id, "candidate_ids": created_candidates, "create_candidate": create_candidate}
    yield data

    async def cleanup():
        async with factory() as session:
            ids = data["candidate_ids"]
            if ids:
                await session.execute(delete(TrustedMine).where(TrustedMine.source_candidate_id.in_(ids)))
                await session.execute(delete(TrustedProject).where(TrustedProject.source_candidate_id.in_(ids)))
                await session.execute(delete(TrustedBorehole).where(TrustedBorehole.source_candidate_id.in_(ids)))
                await session.execute(delete(CoalBlock).where(CoalBlock.source_candidate_id.in_(ids)))
                await session.execute(delete(GeologicalFormation).where(GeologicalFormation.source_candidate_id.in_(ids)))
                await session.execute(delete(Seam).where(Seam.source_candidate_id.in_(ids)))
                await session.execute(delete(TrustedGeologicalMeasurement).where(TrustedGeologicalMeasurement.source_candidate_id.in_(ids)))
                await session.execute(delete(TrustedProductionRecord).where(TrustedProductionRecord.source_candidate_id.in_(ids)))
                await session.execute(delete(TrustedCoordinate).where(TrustedCoordinate.source_candidate_id.in_(ids)))
                await session.execute(delete(CanonicalValueHistory).where(CanonicalValueHistory.source_candidate_id.in_(ids)))
                await session.execute(delete(CanonicalDataConflict).where(CanonicalDataConflict.entity_id.in_(select(TrustedMine.id))))
                entity_ids = set((await session.execute(select(CanonicalRecord.canonical_entity_id).where(CanonicalRecord.candidate_id.in_(ids), CanonicalRecord.canonical_entity_id.is_not(None)))).scalars().all())
                entity_ids.update((await session.execute(select(MappingProposal.proposed_entity_id).where(MappingProposal.candidate_id.in_(ids), MappingProposal.proposed_entity_id.is_not(None)))).scalars().all())
                entity_ids.update((await session.execute(select(MappingProposal.possible_entity_id).where(MappingProposal.candidate_id.in_(ids), MappingProposal.possible_entity_id.is_not(None)))).scalars().all())
                conflict_ids = (await session.execute(select(ExtractionConflictCandidate.conflict_id).where(ExtractionConflictCandidate.candidate_id.in_(ids)))).scalars().all()
                await session.execute(delete(CanonicalRecord).where(CanonicalRecord.candidate_id.in_(ids)))
                await session.execute(delete(MappingProposal).where(MappingProposal.candidate_id.in_(ids)))
                await session.execute(delete(VerificationEvent).where(VerificationEvent.candidate_id.in_(ids)))
                await session.execute(delete(ExtractionConflictCandidate).where(ExtractionConflictCandidate.candidate_id.in_(ids)))
                if conflict_ids:
                    await session.execute(delete(ExtractionConflict).where(ExtractionConflict.id.in_(conflict_ids)))
                await session.execute(delete(CanonicalEntity).where(CanonicalEntity.id.in_(entity_ids)))
                await session.execute(delete(ExtractionCandidate).where(ExtractionCandidate.id.in_(ids)))
            await session.execute(delete(Document).where(Document.id == data["document_id"]))
            await session.execute(delete(AuditLog).where(AuditLog.actor_id == data["user_id"]))
            await session.execute(delete(User).where(User.id == data["user_id"]))
            await session.commit()

    try:
        asyncio.run(cleanup())
    finally:
        asyncio.run(engine.dispose())
        app.dependency_overrides.clear()


def test_candidate_classification_and_unknown():
    candidate = ExtractionCandidate(candidate_type="mine", raw_value="Mine X", normalized_value="Mine X", candidate_metadata={})
    assert classify_candidate(candidate)["classification"] == "MINE"
    measurement = ExtractionCandidate(candidate_type="measurement", raw_value="12", normalized_value="12", candidate_metadata={})
    assert classify_candidate(measurement)["classification"] == "UNKNOWN"
    assert classify_candidate(measurement)["rule"] == "measurement_semantics_missing"
    year_only = ExtractionCandidate(candidate_type="production_record", raw_value="2024",
        normalized_value=None, normalized_numeric_value=None, unit=None, candidate_metadata={})
    assert classify_candidate(year_only)["classification"] == "UNKNOWN"
    assert classify_candidate(year_only)["rule"] == "production_amount_unit_period_required"
    complete_production = ExtractionCandidate(candidate_type="production_record", raw_value="1047523000",
        normalized_value="1047523000", normalized_numeric_value=Decimal("1047523000"), unit="t",
        candidate_metadata={"reporting_period": "2024-25"}, validation_results=[])
    assert classify_candidate(complete_production)["classification"] == "PRODUCTION"


def test_extracted_percentage_share_uses_source_excerpt_not_production_classification():
    candidate = ExtractionCandidate(
        candidate_type="measurement", raw_value="4.40", normalized_value="4.40",
        normalized_numeric_value=Decimal("4.40"), unit="%",
        raw_text=("Share % of all India Production of Public & Private Sector during last Three Years "
                  "Public 95.60% 95.19% 94.54% Private 4.40% 4.81% 5.46% 2022-23 2023-24 2024-25"),
        candidate_metadata={"dimension": "percentage", "source_unit": "%", "table_header": "Table 3.12"},
        validation_results=[],
    )
    result = classify_candidate(candidate)
    assert result == {"classification": "MEASUREMENT", "confidence": 0.92,
                      "rule": "percentage_source_semantics"}


def test_mapping_new_matched_and_possible_match(verification_db):
    async def run():
        candidate_id = await verification_db["create_candidate"](raw_value="Talcher Coal Field")
        async with PossibleSession(verification_db["factory"]) as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            proposal = await propose_mapping(session, candidate)
            assert proposal.status == "NEW"
            assert candidate.classification_status == "CLASSIFIED"
            session.add(CanonicalEntity(entity_type="MINE", canonical_name="Talcher Coalfield", normalized_key=normalize_entity_key("Talcher Coalfield"), details={}))
            await session.commit()
            proposal = await propose_mapping(session, candidate)
            assert proposal.status == "MATCHED"
            candidate.normalized_value = "Talcher Coal Fiel"
            candidate.raw_value = "Talcher Coal Fiel"
            proposal = await propose_mapping(session, candidate)
            assert proposal.status == "POSSIBLE_MATCH"
            await session.commit()
    asyncio.run(run())


class PossibleSession:
    """Small async context adapter to keep test bodies readable."""
    def __init__(self, factory):
        self.factory = factory
        self.session = None
    async def __aenter__(self):
        self.session = self.factory()
        return await self.session.__aenter__()
    async def __aexit__(self, *args):
        return await self.session.__aexit__(*args)


def test_approval_is_idempotent_and_keeps_provenance(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            await claim_candidate(session, candidate_id)
        async with verification_db["factory"]() as session:
            record1 = await approve_candidate(session, candidate_id)
        async with verification_db["factory"]() as session:
            record2 = await approve_candidate(session, candidate_id)
        assert record1.id == record2.id
        async with verification_db["factory"]() as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            assert candidate.verification_status == "VERIFIED"
            assert record1.source_document_id == verification_db["document_id"]
            assert record1.source_version_id == verification_db["version_id"]
            assert record1.source_page_id == verification_db["page_id"]
            assert record1.source_content_id == verification_db["content_id"]
            assert await session.scalar(select(func_count(CanonicalRecord.id)).where(CanonicalRecord.candidate_id == candidate_id)) == 1
            assert await session.scalar(select(func_count(VerificationEvent.id)).where(VerificationEvent.candidate_id == candidate_id)) == 2
    asyncio.run(run())


def test_concurrent_approval_requests_create_one_canonical_record(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            await claim_candidate(session, candidate_id)
        async def approve_once():
            async with verification_db["factory"]() as session:
                return await approve_candidate(session, candidate_id)
        first, second = await asyncio.gather(approve_once(), approve_once())
        assert first.id == second.id
        async with verification_db["factory"]() as session:
            assert await session.scalar(select(func_count(CanonicalRecord.id)).where(CanonicalRecord.candidate_id == candidate_id)) == 1
            assert await session.scalar(select(func_count(CanonicalEntity.id)).where(CanonicalEntity.normalized_key == "talchercoalfield")) == 1
    asyncio.run(run())


def test_rejection_preserves_candidate_and_is_idempotent(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            await reject_candidate(session, candidate_id, reason="Insufficient evidence")
            await reject_candidate(session, candidate_id, reason="Insufficient evidence")
            assert await session.get(ExtractionCandidate, candidate_id) is not None
            candidate_events = (await session.execute(select(VerificationEvent).where(VerificationEvent.candidate_id == candidate_id))).scalars().all()
            assert len(candidate_events) == 1 and candidate_events[0].new_state == "REJECTED"
    asyncio.run(run())


def test_edit_approve_preserves_original_and_records_reason(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            await claim_candidate(session, candidate_id)
        async with verification_db["factory"]() as session:
            record = await approve_candidate(session, candidate_id, reason="Verified against source page", edited_value={"raw_value": "Talcher Coal Field", "normalized_value": "Talcher Coal Field"})
        assert record.original_value["raw_value"] == "Talcher Coalfield"
        assert record.accepted_value["raw_value"] == "Talcher Coal Field"
        assert record.reason == "Verified against source page"
    asyncio.run(run())


def test_mark_unresolved(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            candidate = await unresolved_candidate(session, candidate_id, reason="Needs source review")
            assert candidate.verification_status == "UNRESOLVED"
            event = await session.scalar(select(VerificationEvent).where(VerificationEvent.candidate_id == candidate_id))
            assert event.action == "MARK_UNRESOLVED"
    asyncio.run(run())


def test_conflict_detection_and_resolution_are_audited(verification_db):
    async def run():
        first = await verification_db["create_candidate"](candidate_type="production_record", raw_value="120", normalized_value="120", metadata={"semantic_key": "mine-x:2025-production"})
        second = await verification_db["create_candidate"](candidate_type="production_record", raw_value="125", normalized_value="125", metadata={"semantic_key": "mine-x:2025-production"})
        async with verification_db["factory"]() as session:
            other = await session.get(ExtractionCandidate, second)
            conflicts = await detect_candidate_conflicts(session, other)
            assert len(conflicts) == 1
            await session.commit()
            conflict_id = conflicts[0].id
            resolved = await resolve_conflict(session, conflict_id, status="ACCEPTED_AS_SOURCE_VARIATION", resolution="Source reports differ")
            assert resolved.status == "ACCEPTED_AS_SOURCE_VARIATION"
            events = (await session.execute(select(VerificationEvent).where(VerificationEvent.action == "CONFLICT_RESOLVED"))).scalars().all()
            assert len(events) == 2
            repeated = await resolve_conflict(session, conflict_id, status="RESOLVED", resolution="repeat")
            assert repeated.resolution == "Source reports differ"
    asyncio.run(run())


def test_invalid_and_missing_candidates_cannot_be_approved(verification_db):
    async def run():
        invalid = await verification_db["create_candidate"](candidate_type="coordinate", raw_value="181, 20", normalized_value=None, metadata={"longitude": "181", "latitude": "20"}, validation=[{"rule": "coordinate_range", "status": "FAILED"}])
        missing = await verification_db["create_candidate"](candidate_type="mine", raw_value=None, normalized_value=None)
        async with verification_db["factory"]() as session:
            await claim_candidate(session, invalid)
            await claim_candidate(session, missing)
        with pytest.raises(ValueError):
            async with verification_db["factory"]() as session:
                await approve_candidate(session, invalid)
        with pytest.raises(ValueError):
            async with verification_db["factory"]() as session:
                await approve_candidate(session, missing)
    asyncio.run(run())


def test_verification_queue_pagination_filter_and_provenance(verification_db):
    asyncio.run(verification_db["create_candidate"]())
    asyncio.run(verification_db["create_candidate"](candidate_type="borehole", raw_value="BH-22"))
    from app.db.session import get_db as original_get_db
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    app.dependency_overrides[original_get_db] = test_db_dependency
    with TestClient(app) as client:
        unauthenticated = client.post(f"/api/v1/verification/candidates/{verification_db['candidate_ids'][0]}/claim")
        assert unauthenticated.status_code == 401
        candidate_id = verification_db["candidate_ids"][0]
        assert client.get("/api/v1/verification/queue").status_code == 401
        assert client.get(f"/api/v1/verification/candidates/{candidate_id}").status_code == 401
        assert client.get(f"/api/v1/verification/candidates/{candidate_id}/provenance").status_code == 401
        assert client.get("/api/v1/verification/conflicts").status_code == 401
        assert client.get(f"/api/v1/verification/conflicts/{uuid.UUID(int=0)}").status_code == 401
        assert client.get("/api/v1/canonical/entities").status_code == 401
        assert client.get(f"/api/v1/canonical/entities/{uuid.UUID(int=0)}").status_code == 401
        assert client.get("/api/v1/audit").status_code == 401
        assert client.post("/api/v1/verification/classify", json={}).status_code == 401
        login = client.post("/api/v1/auth/token", data={"username": f"test-{verification_db['user_id']}", "password": "Test-only-password-123!"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.get("/api/v1/verification/queue", headers=headers, params={"page": 1, "page_size": 1, "verification_status": "PENDING", "document_id": str(verification_db["document_id"])})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2 and len(data["items"]) == 1
        assert client.get("/api/v1/verification/conflicts", headers=headers).status_code == 200
        assert client.get("/api/v1/canonical/entities", headers=headers).status_code == 200
        candidate_id = data["items"][0]["candidate"]["id"]
        candidate = client.get(f"/api/v1/verification/candidates/{candidate_id}", headers=headers)
        assert candidate.status_code == 200
        assert candidate.json()["candidate"]["id"] == candidate_id
        provenance = client.get(f"/api/v1/verification/candidates/{candidate_id}/provenance", headers=headers)
        assert provenance.status_code == 200
        source = provenance.json()["source"]
        assert source["document_id"] == str(verification_db["document_id"])
        assert source["page_number"] == 1
        filtered = client.get("/api/v1/verification/queue", headers=headers, params={"document_id": str(verification_db["document_id"]), "classification_status": "NOT_CLASSIFIED", "conflict_status": "NONE", "page_size": 10})
        assert filtered.status_code == 200 and filtered.json()["total"] == 2
        claimed = client.post(f"/api/v1/verification/candidates/{candidate_id}/claim", headers=headers)
        assert claimed.status_code == 200 and claimed.json()["verification_status"] == "IN_REVIEW"
        approved = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", headers=headers, json={"reason": "Reviewed synthetic test source"})
        repeated = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", headers=headers, json={"reason": "Repeated request"})
        assert approved.status_code == 200 and approved.json()["verification_status"] == "VERIFIED"
        assert repeated.json()["canonical_record_id"] == approved.json()["canonical_record_id"]
        entity_id = approved.json()["canonical_entity_id"]
        assert client.get(f"/api/v1/canonical/entities/{entity_id}", headers=headers).status_code == 200
        audit = client.get("/api/v1/audit?page=1&page_size=100", headers=headers)
        assert audit.status_code == 200, audit.text
        audit_items = audit.json()["items"]
        assert {item["action"] for item in audit_items} >= {"CLAIM", "APPROVE", "CANONICALIZE"}
        candidate_events = [item for item in audit_items if item["entity_id"] == candidate_id
            and item["action"] in {"CLAIM", "APPROVE"}]
        canonical_events = [item for item in audit_items if item["action"] == "CANONICALIZE"
            and item["details"].get("candidate_id") == candidate_id]
        assert candidate_events and all(item["actor_id"] == str(verification_db["user_id"])
            for item in candidate_events)
        assert canonical_events and all(item["actor_id"] == str(verification_db["user_id"])
            for item in canonical_events)


def test_verified_production_appears_in_fresh_analytics_response(verification_db):
    """Prove extraction of source-backed report cells through human approval into Analytics."""
    table_id = uuid.uuid4()
    async def extract_production_candidate():
        async with verification_db["factory"]() as session:
            session.add(ExtractedTable(id=table_id, document_page_id=verification_db["page_id"], table_order=1))
            for row_index, values in enumerate((
                ("Production of Raw Coal in 2024-25 (MT)", "", "", ""),
                ("Sector", "Coking", "Non-Coking", "Total Coal"),
                ("All India", "66.470", "981.053", "1047.523"),
            )):
                for column_index, value in enumerate(values):
                    session.add(ExtractedTableCell(table_id=table_id, row_index=row_index,
                        column_index=column_index, raw_value=value, normalized_value=value))
            session.add(ExtractedContent(document_page_id=verification_db["page_id"], content_type="source_glossary",
                text="MT = Million Tonnes"))
            await session.commit()
        async with verification_db["factory"]() as session:
            await extract_document_version(session, verification_db["document_id"])
            candidates = list((await session.scalars(select(ExtractionCandidate).where(
                ExtractionCandidate.document_version_id == verification_db["version_id"],
                ExtractionCandidate.candidate_type == "production_record"))).all())
            candidate = next(row for row in candidates if row.candidate_metadata.get("commodity") == "Total Coal")
            assert candidate.verification_status == "PENDING"
            assert candidate.raw_value == "1047.523"
            assert candidate.normalized_numeric_value == Decimal("1047523000.000000")
            assert candidate.unit == "t"
            assert candidate.candidate_metadata["reporting_period"] == "2024-25"
            assert candidate.source_page_id == verification_db["page_id"]
            assert candidate.source_table_id == table_id
            assert candidate.source_cell_id is not None
            verification_db["candidate_ids"].extend(row.id for row in candidates)
            return candidate.id
    candidate_id = asyncio.run(extract_production_candidate())
    from app.core.auth import get_current_user
    from app.db.session import get_db as original_get_db
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])
    app.dependency_overrides[original_get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer integration-test-token"}
            before = client.get("/api/v1/analytics/production?year=2024", headers=headers)
            assert before.status_code == 200
            before_count = before.json()["record_count"]
            assert client.post(f"/api/v1/verification/candidates/{candidate_id}/claim", headers=headers).status_code == 200
            approved = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", headers=headers,
                json={"reason": "Production candidate verified for integration test"})
            assert approved.status_code == 200, approved.text
            after = client.get("/api/v1/analytics/production?year=2024", headers=headers)
            assert after.status_code == 200, after.text
            result = after.json()
            assert result["status"] == "OK"
            assert result["record_count"] == before_count + 1
            assert any(str(candidate_id) == source["source_candidate_id"]
                for item in result["items"] for source in item["provenance"])
    finally:
        app.dependency_overrides.clear()


def test_in_review_candidate_approval_recomputes_valid_production_classification(verification_db):
    """A valid review candidate approves even when its persisted classification is stale."""
    candidate_id = asyncio.run(verification_db["create_candidate"](
        candidate_type="production_record", raw_value="125000", normalized_value="125000",
        normalized_numeric_value=Decimal("125000"),
        metadata={"reporting_period": "2024-25", "commodity": "Source-backed coal production"},
        validation=[{"rule": "production_nonnegative", "status": "PASSED"}],
    ))

    async def prepare_review_candidate():
        async with verification_db["factory"]() as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.verification_status = "IN_REVIEW"
            candidate.classification = "UNKNOWN"
            candidate.classification_status = "UNKNOWN"
            candidate.mapping_status = "UNRESOLVED"
            await session.commit()
    asyncio.run(prepare_review_candidate())

    from app.core.auth import get_current_user
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])
    app.dependency_overrides[get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/verification/candidates/{candidate_id}/approve",
                headers={"Authorization": "Bearer integration-test-token"},
                json={"reason": "Reviewed against the source evidence"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["verification_status"] == "VERIFIED"
        async def assert_trusted_record():
            async with verification_db["factory"]() as session:
                candidate = await session.get(ExtractionCandidate, candidate_id)
                record = await session.scalar(select(TrustedProductionRecord).where(
                    TrustedProductionRecord.source_candidate_id == candidate_id))
                assert candidate.verification_status == "VERIFIED"
                assert candidate.classification == "PRODUCTION"
                assert record is not None
                assert record.production_value == Decimal("125000")
        asyncio.run(assert_trusted_record())
    finally:
        app.dependency_overrides.clear()


def test_invalid_in_review_candidate_returns_actionable_409_detail(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"](
        candidate_type="measurement", raw_value="11.73", normalized_value="11.73",
        normalized_numeric_value=Decimal("11.73"), unit="%", metadata={"dimension": "percentage"},
    ))
    async def mark_in_review():
        async with verification_db["factory"]() as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.verification_status = "IN_REVIEW"
            await session.commit()
    asyncio.run(mark_in_review())

    from app.core.auth import get_current_user
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])
    app.dependency_overrides[get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/verification/candidates/{candidate_id}/approve",
                headers={"Authorization": "Bearer integration-test-token"},
                json={"reason": "Trust"},
            )
            assert response.status_code == 409
            assert response.json()["detail"] == (
                "Candidate classification is ambiguous; source evidence does not identify a supported data type"
            )
        async def assert_not_approved():
            async with verification_db["factory"]() as session:
                candidate = await session.get(ExtractionCandidate, candidate_id)
                assert candidate.verification_status == "IN_REVIEW"
                assert await session.scalar(select(CanonicalRecord).where(
                    CanonicalRecord.candidate_id == candidate_id)) is None
        asyncio.run(assert_not_approved())
    finally:
        app.dependency_overrides.clear()


def test_in_review_source_percentage_approval_is_nonproduction_and_needs_no_entity_mapping(verification_db):
    raw_text = ("Share % of all India Production of Public & Private Sector during last Three Years "
                "Public 95.60% 95.19% 94.54% Private 4.40% 4.81% 5.46% 2022-23 2023-24 2024-25")
    candidate_id = asyncio.run(verification_db["create_candidate"](
        candidate_type="measurement", raw_value="4.40", normalized_value="4.40",
        normalized_numeric_value=Decimal("4.40"), unit="%", raw_text=raw_text,
        metadata={"dimension": "percentage", "source_unit": "%", "row_index": 29,
                  "column_index": 0, "table_header": "Table 3.12", "rule": "number_unit_pattern"},
    ))
    async def mark_in_review():
        async with verification_db["factory"]() as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.verification_status = "IN_REVIEW"
            candidate.classification = "UNKNOWN"
            candidate.classification_status = "UNKNOWN"
            candidate.mapping_status = "UNRESOLVED"
            await session.commit()
    asyncio.run(mark_in_review())

    async def run_approval():
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            record = await approve_candidate(session, candidate_id, actor_id=actor.id,
                reason="Source chart identifies this as private-sector share")
            candidate = await session.get(ExtractionCandidate, candidate_id)
            trusted_production = await session.scalar(select(TrustedProductionRecord).where(
                TrustedProductionRecord.source_candidate_id == candidate_id))
            assert record.record_type == "MEASUREMENT"
            assert candidate.verification_status == "VERIFIED"
            assert candidate.classification == "MEASUREMENT"
            assert candidate.classification_confidence == Decimal("0.92")
            assert candidate.mapping_status == "NOT_REQUIRED"
            assert candidate.match_status == "NOT_REQUIRED"
            assert trusted_production is None
    asyncio.run(run_approval())


def test_ambiguous_measurement_stays_unapprovable_after_refresh(verification_db):
    candidate_id = asyncio.run(verification_db["create_candidate"](
        candidate_type="measurement", raw_value="4.40", normalized_value="4.40",
        normalized_numeric_value=Decimal("4.40"), unit="%",
        raw_text="4.40%", metadata={"dimension": "percentage", "source_unit": "%"},
    ))
    async def run():
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.verification_status = "IN_REVIEW"
            await session.commit()
            with pytest.raises(ValueError, match="Candidate classification is ambiguous; source evidence"):
                await approve_candidate(session, candidate_id, actor_id=actor.id, reason="Trust")
        async with verification_db["factory"]() as session:
            candidate = await session.get(ExtractionCandidate, candidate_id)
            assert candidate.verification_status == "IN_REVIEW"
            assert candidate.classification == "UNKNOWN"
            assert candidate.mapping_status == "UNRESOLVED"
    asyncio.run(run())


def test_coordinate_approval_materializes_trusted_gis_row_and_layer(verification_db):
    """The authenticated approval flow must publish verified coordinates to GIS."""
    table_id, cell_id = uuid.uuid4(), uuid.uuid4()
    async def create_source_and_candidate():
        async with verification_db["factory"]() as session:
            session.add(ExtractedTable(id=table_id, document_page_id=verification_db["page_id"], table_order=1))
            for row_index, values in enumerate((
                ("Entity Type", "Name", "Latitude", "Longitude"),
                ("VERIFIED_COORDINATE", "Mine Point A", "12.5", "77.25"),
            )):
                for column_index, value in enumerate(values):
                    cell = ExtractedTableCell(table_id=table_id, row_index=row_index,
                        column_index=column_index, raw_value=value, normalized_value=value)
                    if row_index == 1 and column_index == 1:
                        cell.id = cell_id
                    session.add(cell)
            await session.commit()
        async with verification_db["factory"]() as session:
            await extract_document_version(session, verification_db["document_id"])
            candidate = await session.scalar(select(ExtractionCandidate).where(
                ExtractionCandidate.document_version_id == verification_db["version_id"],
                ExtractionCandidate.candidate_type == "coordinate"))
            assert candidate is not None and candidate.verification_status == "PENDING"
            assert candidate.raw_value == "77.25,12.5"
            assert candidate.candidate_metadata["source_cells"]["Latitude"]
            assert candidate.candidate_metadata["source_cells"]["Longitude"]
            verification_db["candidate_ids"].append(candidate.id)
            return candidate.id
    candidate_id = asyncio.run(create_source_and_candidate())

    from app.core.auth import get_current_user
    from app.db.session import get_db as original_get_db
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])

    app.dependency_overrides[original_get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer integration-test-token"}
            claimed = client.post(f"/api/v1/verification/candidates/{candidate_id}/claim", headers=headers)
            assert claimed.status_code == 200
            approved = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", headers=headers,
                json={"reason": "Coordinate confirmed against source evidence"})
            repeated = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", headers=headers,
                json={"reason": "Idempotent approval retry"})
            assert approved.status_code == 200, approved.text
            assert repeated.status_code == 200
            assert repeated.json()["canonical_record_id"] == approved.json()["canonical_record_id"]

            layers = client.get("/api/v1/gis/layers", headers=headers)
            assert layers.status_code == 200, layers.text
            payload = layers.json()
            assert payload["status"] == "OK"
            feature = next(item for item in payload["features"]
                if item["properties"]["entity_type"] == "VERIFIED_COORDINATE"
                and item["properties"]["provenance"]["source_candidate_id"] == str(candidate_id))
            assert feature["geometry"] == {"type": "Point", "coordinates": [77.25, 12.5]}
            assert feature["properties"]["coordinate_system"] == "EPSG:4326"
            provenance = feature["properties"]["provenance"]
            assert provenance["source_document_id"] == str(verification_db["document_id"])
            assert provenance["source_version_id"] == str(verification_db["version_id"])
            assert provenance["source_page_id"] == str(verification_db["page_id"])
            assert provenance["source_content_id"] is None
            assert provenance["source_table_id"] == str(table_id)
            assert provenance["source_cell_id"] == str(cell_id)
            assert provenance["verification_id"] == str(approved.json()["canonical_record_id"])
            assert provenance["verifier_id"] == str(verification_db["user_id"])

        async def verify_persistence():
            from sqlalchemy import func
            async with verification_db["factory"]() as session:
                coordinate = await session.scalar(select(TrustedCoordinate).where(
                    TrustedCoordinate.source_candidate_id == candidate_id))
                assert coordinate is not None
                assert coordinate.coordinate_system == "EPSG:4326"
                assert str(await session.scalar(select(func.ST_AsText(coordinate.geometry)))) == "POINT(77.25 12.5)"
                assert await session.scalar(select(func.ST_SRID(coordinate.geometry))) == 4326
                assert coordinate.verification_id == uuid.UUID(approved.json()["canonical_record_id"])
                assert coordinate.verifier_id == verification_db["user_id"]
                assert await session.scalar(select(func.count()).select_from(TrustedCoordinate).where(
                    TrustedCoordinate.source_candidate_id == candidate_id)) == 1
        asyncio.run(verify_persistence())
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(("entity_type", "candidate_type", "name", "trusted_model", "layer_type"), [
    ("MINE", "mine", "Source Mine A", TrustedMine, "MINE"),
    ("BOREHOLE", "borehole", "BH-99", TrustedBorehole, "BOREHOLE"),
])
def test_mine_and_borehole_source_rows_materialize_and_reach_gis(verification_db,
        entity_type, candidate_type, name, trusted_model, layer_type):
    """A source table row must reach GIS only through claim, approval, and canonicalization."""
    table_id = uuid.uuid4()

    async def extract_row_candidate():
        async with verification_db["factory"]() as session:
            session.add(ExtractedTable(id=table_id,
                document_page_id=verification_db["page_id"], table_order=1))
            for row_index, values in enumerate((
                ("Entity Type", "Name", "Latitude", "Longitude"),
                (entity_type, name, "12.345", "77.123"),
            )):
                for column_index, value in enumerate(values):
                    session.add(ExtractedTableCell(table_id=table_id, row_index=row_index,
                        column_index=column_index, raw_value=value, normalized_value=value))
            await session.commit()
        async with verification_db["factory"]() as session:
            await extract_document_version(session, verification_db["document_id"])
            candidate = await session.scalar(select(ExtractionCandidate).where(
                ExtractionCandidate.document_version_id == verification_db["version_id"],
                ExtractionCandidate.candidate_type == candidate_type,
                ExtractionCandidate.candidate_metadata["extraction_source"].astext == "table_row"))
            assert candidate is not None
            assert candidate.verification_status == "PENDING"
            assert candidate.candidate_metadata["latitude"] == "12.345"
            assert candidate.candidate_metadata["longitude"] == "77.123"
            assert candidate.source_table_id == table_id
            verification_db["candidate_ids"].append(candidate.id)
            return candidate.id

    candidate_id = asyncio.run(extract_row_candidate())
    from app.core.auth import get_current_user
    from app.db.session import get_db as original_get_db

    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session

    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])

    app.dependency_overrides[original_get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer integration-test-token"}
            before_approval = client.get("/api/v1/gis/layers", headers=headers)
            assert before_approval.status_code == 200
            assert all(item["properties"]["provenance"]["source_candidate_id"] != str(candidate_id)
                for item in before_approval.json()["features"])
            claim = client.post(f"/api/v1/verification/candidates/{candidate_id}/claim", headers=headers)
            assert claim.status_code == 200, claim.text
            approved = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve",
                headers=headers, json={"reason": "Source row coordinates verified for GIS integration test"})
            assert approved.status_code == 200, approved.text
            repeated = client.post(f"/api/v1/verification/candidates/{candidate_id}/approve",
                headers=headers, json={"reason": "Idempotency check"})
            assert repeated.status_code == 200
            layers = client.get("/api/v1/gis/layers", headers=headers)
            assert layers.status_code == 200, layers.text
            feature = next(item for item in layers.json()["features"]
                if item["properties"]["entity_type"] == layer_type
                and item["properties"]["provenance"]["source_candidate_id"] == str(candidate_id))
            assert feature["geometry"] == {"type": "Point", "coordinates": [77.123, 12.345]}
            assert feature["properties"]["geometry_valid"] is True
            assert feature["properties"]["srid"] == 4326
            assert feature["properties"]["provenance"]["source_table_id"] == str(table_id)
            assert feature["properties"]["provenance"]["verification_id"] == str(approved.json()["canonical_record_id"])
            assert feature["properties"]["provenance"]["verifier_id"] == str(verification_db["user_id"])

        async def verify_materialized_row():
            from sqlalchemy import func
            async with verification_db["factory"]() as session:
                row = await session.scalar(select(trusted_model).where(
                    trusted_model.source_candidate_id == candidate_id))
                assert row is not None
                assert row.source_document_id == verification_db["document_id"]
                assert row.source_version_id == verification_db["version_id"]
                assert row.source_page_id == verification_db["page_id"]
                assert row.source_table_id == table_id
                assert row.verification_id == uuid.UUID(approved.json()["canonical_record_id"])
                assert row.verifier_id == verification_db["user_id"]
                assert await session.scalar(select(func.ST_SRID(row.location))) == 4326
                assert str(await session.scalar(select(func.ST_AsText(row.location)))) == "POINT(77.123 12.345)"
                assert await session.scalar(select(func.count()).select_from(trusted_model).where(
                    trusted_model.source_candidate_id == candidate_id)) == 1
        asyncio.run(verify_materialized_row())
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(("entity_type", "candidate_type", "name", "trusted_model"), [
    ("MINE", "mine", "Rejected Mine", TrustedMine),
    ("BOREHOLE", "borehole", "Rejected Borehole", TrustedBorehole),
])
def test_rejected_spatial_candidates_never_appear_in_gis(verification_db,
        entity_type, candidate_type, name, trusted_model):
    table_id = uuid.uuid4()

    async def extract_candidate():
        async with verification_db["factory"]() as session:
            session.add(ExtractedTable(id=table_id,
                document_page_id=verification_db["page_id"], table_order=1))
            for row_index, values in enumerate((
                ("Entity Type", "Name", "Latitude", "Longitude"),
                (entity_type, name, "1.234", "2.345"),
            )):
                for column_index, value in enumerate(values):
                    session.add(ExtractedTableCell(table_id=table_id, row_index=row_index,
                        column_index=column_index, raw_value=value, normalized_value=value))
            await session.commit()
        async with verification_db["factory"]() as session:
            await extract_document_version(session, verification_db["document_id"])
            candidate = await session.scalar(select(ExtractionCandidate).where(
                ExtractionCandidate.document_version_id == verification_db["version_id"],
                ExtractionCandidate.candidate_type == candidate_type,
                ExtractionCandidate.candidate_metadata["extraction_source"].astext == "table_row"))
            assert candidate is not None and candidate.verification_status == "PENDING"
            verification_db["candidate_ids"].append(candidate.id)
            return candidate.id

    candidate_id = asyncio.run(extract_candidate())
    from app.core.auth import get_current_user
    from app.db.session import get_db as original_get_db

    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session

    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])

    app.dependency_overrides[original_get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer integration-test-token"}
            rejected = client.post(f"/api/v1/verification/candidates/{candidate_id}/reject",
                headers=headers, json={"reason": "Rejected in GIS exclusion regression test"})
            assert rejected.status_code == 200, rejected.text
            layers = client.get("/api/v1/gis/layers", headers=headers)
            assert layers.status_code == 200
            assert all(item["properties"]["provenance"]["source_candidate_id"] != str(candidate_id)
                for item in layers.json()["features"])
        async def verify_no_trusted_row():
            async with verification_db["factory"]() as session:
                assert await session.scalar(select(trusted_model.id).where(
                    trusted_model.source_candidate_id == candidate_id)) is None
        asyncio.run(verify_no_trusted_row())
    finally:
        app.dependency_overrides.clear()


def test_canonicalization_requires_verification_and_is_idempotent(verification_db):
    from app.services.canonicalization import canonicalize_candidate
    candidate_id = asyncio.run(verification_db["create_candidate"](raw_value="Mina A"))
    async def run():
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            with pytest.raises(ValueError, match="Only VERIFIED"):
                await canonicalize_candidate(session, candidate_id, actor)
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            await claim_candidate(session, candidate_id, actor.id)
            await approve_candidate(session, candidate_id, actor_id=actor.id)
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            first = await canonicalize_candidate(session, candidate_id, actor)
            second = await canonicalize_candidate(session, candidate_id, actor)
            assert first["entity_id"] == second["entity_id"]
            assert second["idempotent"] is True
            row = await session.get(TrustedMine, first["entity_id"])
            assert row.source_document_id == verification_db["document_id"]
            assert row.source_version_id == verification_db["version_id"]
            assert row.source_page_id == verification_db["page_id"]
            provenance = await session.scalar(select(CanonicalValueHistory).where(CanonicalValueHistory.source_candidate_id == candidate_id))
            assert provenance.verifier_id == verification_db["user_id"]
            assert await session.scalar(select(__import__("sqlalchemy").func.count()).select_from(AuditLog).where(AuditLog.actor_id == verification_db["user_id"], AuditLog.action == "CANONICALIZE")) == 1
            return first
    first = asyncio.run(run())

    from app.core.auth import get_current_user
    from app.db.session import get_db as original_get_db
    async def test_db_dependency():
        async with verification_db["factory"]() as session:
            yield session
    async def test_user_dependency():
        async with verification_db["factory"]() as session:
            return await session.get(User, verification_db["user_id"])
    app.dependency_overrides[original_get_db] = test_db_dependency
    app.dependency_overrides[get_current_user] = test_user_dependency
    with TestClient(app) as client:
        listing = client.get("/api/v1/canonical/mines", params={"page": 1, "page_size": 5, "sort_by": "canonical_name"})
        assert listing.status_code == 200 and listing.json()["total"] == 1
        evidence = client.get(f"/api/v1/canonical/MINE/{first['entity_id']}/provenance")
        assert evidence.status_code == 200
        source = evidence.json()["items"][0]
        assert source["document_id"] == str(verification_db["document_id"])
        assert source["page_id"] == str(verification_db["page_id"])
        assert source["source_content_id"] == str(verification_db["content_id"])


@pytest.mark.parametrize("blocked_status", ["REJECTED", "UNRESOLVED"])
def test_non_verified_status_never_canonicalizes(verification_db, blocked_status):
    from app.services.canonicalization import canonicalize_candidate
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.verification_status = blocked_status
            await session.commit()
            with pytest.raises(ValueError, match="Only VERIFIED"):
                await canonicalize_candidate(session, candidate_id, actor)
    asyncio.run(run())


@pytest.mark.parametrize("identity_status", ["POSSIBLE_MATCH", "CONFLICT", "UNRESOLVED"])
def test_identity_requires_human_resolution(verification_db, identity_status):
    from app.services.canonicalization import canonicalize_candidate
    candidate_id = asyncio.run(verification_db["create_candidate"]())
    async def run():
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            await claim_candidate(session, candidate_id, actor.id)
            await approve_candidate(session, candidate_id, actor_id=actor.id)
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            candidate = await session.get(ExtractionCandidate, candidate_id)
            candidate.mapping_status = identity_status
            await session.commit()
            with pytest.raises(ValueError, match="identity requires human resolution"):
                await canonicalize_candidate(session, candidate_id, actor)
    asyncio.run(run())


def test_normalized_name_and_alias_identity_are_deterministic(verification_db):
    from app.services.canonicalization import canonicalize_candidate, normalize_name
    assert normalize_name("Talcher Coalfield") == normalize_name("Talcher Coal Field")
    first_id = asyncio.run(verification_db["create_candidate"](raw_value="Talcher Coalfield"))
    second_id = asyncio.run(verification_db["create_candidate"](raw_value="Talcher Coal Field"))
    async def verify(candidate_id):
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            await claim_candidate(session, candidate_id, actor.id)
            await approve_candidate(session, candidate_id, actor_id=actor.id)
            return actor
    async def run():
        await verify(first_id)
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            first = await canonicalize_candidate(session, first_id, actor)
        await verify(second_id)
        async with verification_db["factory"]() as session:
            actor = await session.get(User, verification_db["user_id"])
            second = await canonicalize_candidate(session, second_id, actor)
            assert first["entity_id"] == second["entity_id"]
            assert await session.scalar(select(__import__("sqlalchemy").func.count()).select_from(TrustedMine)) == 1
    asyncio.run(run())


def test_identity_rules_use_codes_names_aliases_parent_and_proximity():
    from types import SimpleNamespace
    from app.services.canonicalization import resolve_identity
    row = SimpleNamespace(canonical_name="Talcher Coalfield", aliases=["Talcher CF"], domain_code="TB-01",
        attributes={"parent_entity_id": "parent-a", "latitude": 20.95, "longitude": 85.23})
    assert resolve_identity("Talcher CF", {}, [row])[0] == "MATCHED"
    assert resolve_identity("Alternative label", {"code": "TB-01"}, [row])[0] == "POSSIBLE_MATCH"
    assert resolve_identity("Talcher Coalfield", {"parent_entity_id": "parent-b"}, [row])[0] == "CONFLICT"
    assert resolve_identity("Talcher Coal Fiel", {"parent_entity_id": "parent-a"}, [row])[0] == "POSSIBLE_MATCH"
    assert resolve_identity("Unknown", {"latitude": 20.951, "longitude": 85.231}, [row])[0] == "POSSIBLE_MATCH"
    assert resolve_identity("", {}, [])[0] == "UNRESOLVED"


def func_count(column):
    from sqlalchemy import func
    return func.count(column)
