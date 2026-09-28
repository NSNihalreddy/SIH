import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.auth import require_roles
from app.models.identity import User
from app.models.canonical import CanonicalEntity, CanonicalRecord, ExtractionConflict, ExtractionConflictCandidate, MappingProposal, VerificationEvent
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.extraction import ExtractionCandidate
from app.models.intelligence import ExtractedContent, ExtractedTableCell
from app.schemas.verification import ClassificationBatchRequest, ConflictResolutionRequest, EditApproveRequest, ReviewRequest
from app.services.verification import approve_candidate, claim_candidate, classify_batch, reject_candidate, resolve_conflict, unresolved_candidate

router = APIRouter(prefix="/api/v1", tags=["verification"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Operator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


async def _candidate_detail(session: AsyncSession, candidate_id: uuid.UUID) -> dict[str, Any]:
    candidate = await session.get(ExtractionCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    document = await session.get(Document, candidate.document_id)
    version = await session.get(DocumentVersion, candidate.document_version_id)
    page = await session.get(DocumentPage, candidate.source_page_id) if candidate.source_page_id else None
    content = await session.get(ExtractedContent, candidate.source_content_id) if candidate.source_content_id else None
    cell = await session.get(ExtractedTableCell, candidate.source_cell_id) if candidate.source_cell_id else None
    proposal = await session.scalar(select(MappingProposal).where(MappingProposal.candidate_id == candidate.id))
    conflicts = (await session.execute(select(ExtractionConflict).join(ExtractionConflictCandidate, ExtractionConflictCandidate.conflict_id == ExtractionConflict.id).where(ExtractionConflictCandidate.candidate_id == candidate.id).order_by(ExtractionConflict.created_at.desc()))).scalars().all()
    return jsonable_encoder({
        "candidate": candidate,
        "source": {
            "document_id": candidate.document_id,
            "document_name": document.original_filename if document else None,
            "document_version_id": candidate.document_version_id,
            "version_number": version.version_number if version else None,
            "page_id": candidate.source_page_id,
            "page_number": page.page_number if page else None,
            "page_text": (page.text or "")[:4000] if page else None,
            "source_content_id": candidate.source_content_id,
            "source_text": (content.text or "")[:4000] if content else None,
            "source_table_id": candidate.source_table_id,
            "source_cell_id": candidate.source_cell_id,
            "source_cell": {"row_index": cell.row_index, "column_index": cell.column_index, "raw_value": cell.raw_value} if cell else None,
            "raw_extracted_text": candidate.raw_text,
        },
        "mapping_proposal": proposal,
        "conflicts": conflicts,
    })


@router.post("/verification/classify")
async def classify_candidates(request: ClassificationBatchRequest, actor: Operator, session: AsyncSession = Depends(get_db)) -> dict:
    return await classify_batch(session, limit=request.limit, document_id=request.document_id)


@router.get("/verification/queue")
async def verification_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    verification_status: str | None = Query(None, pattern="^(PENDING|IN_REVIEW|VERIFIED|REJECTED|UNRESOLVED)$"),
    search: str | None = Query(None, max_length=200),
    classification: str | None = Query(None, max_length=50),
    classification_status: str | None = Query(None, pattern="^(NOT_CLASSIFIED|CLASSIFIED|UNKNOWN)$"),
    document_id: uuid.UUID | None = None,
    source_page_id: uuid.UUID | None = None,
    conflict_status: str | None = Query(None, pattern="^(OPEN|UNDER_REVIEW|RESOLVED|ACCEPTED_AS_SOURCE_VARIATION|NONE)$"),
    validation_status: str | None = Query(None, pattern="^(PASSED|FAILED|WARNING)$"),
    sort_by: str = Query("created_at", pattern="^(created_at|candidate_type|classification|confidence)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER")),
) -> dict:
    query = select(ExtractionCandidate)
    count_query = select(func.count()).select_from(ExtractionCandidate)
    if verification_status:
        query = query.where(ExtractionCandidate.verification_status == verification_status)
        count_query = count_query.where(ExtractionCandidate.verification_status == verification_status)

    # GLOBAL VERIFICATION SEARCH
    # Numeric input such as "237" means SOURCE PAGE 237 (or an exact
    # extracted value 237). It is NOT searched inside OCR/source text.
    # This avoids unrelated candidates being returned just because
    # "237" happens to appear somewhere in their evidence text.
    if search and search.strip():
        search_text = search.strip()

        page_exists = (
            select(DocumentPage.id)
            .where(
                DocumentPage.id == ExtractionCandidate.source_page_id,
                DocumentPage.page_number == int(search_text)
            )
            .exists()
        ) if search_text.isdigit() else None

        if page_exists is not None:
            exact_value = search_text.lower()
            search_condition = or_(
                page_exists,
                func.lower(cast(ExtractionCandidate.raw_value, String)) == exact_value,
                func.lower(cast(ExtractionCandidate.normalized_value, String)) == exact_value,
            )
        else:
            term = f"%{search_text.lower()}%"

            document_exists = (
                select(Document.id)
                .where(
                    Document.id == ExtractionCandidate.document_id,
                    func.lower(Document.original_filename).like(term),
                )
                .exists()
            )

            page_text_exists = (
                select(DocumentPage.id)
                .where(
                    DocumentPage.id == ExtractionCandidate.source_page_id,
                    func.lower(cast(DocumentPage.page_number, String)).like(term),
                )
                .exists()
            )

            content_exists = (
                select(ExtractedContent.id)
                .where(
                    ExtractedContent.id == ExtractionCandidate.source_content_id,
                    func.lower(ExtractedContent.text).like(term),
                )
                .exists()
            )

            search_condition = or_(
                func.lower(cast(ExtractionCandidate.candidate_type, String)).like(term),
                func.lower(cast(ExtractionCandidate.raw_value, String)).like(term),
                func.lower(cast(ExtractionCandidate.normalized_value, String)).like(term),
                func.lower(cast(ExtractionCandidate.raw_text, String)).like(term),
                func.lower(cast(ExtractionCandidate.classification, String)).like(term),
                document_exists,
                page_text_exists,
                content_exists,
            )

        query = query.where(search_condition)
        count_query = count_query.where(search_condition)

    if classification:
        query = query.where(ExtractionCandidate.classification == classification.upper())
        count_query = count_query.where(ExtractionCandidate.classification == classification.upper())
    if classification_status:
        query = query.where(ExtractionCandidate.classification_status == classification_status)
        count_query = count_query.where(ExtractionCandidate.classification_status == classification_status)
    if document_id:
        query = query.where(ExtractionCandidate.document_id == document_id)
        count_query = count_query.where(ExtractionCandidate.document_id == document_id)
    if source_page_id:
        query = query.where(ExtractionCandidate.source_page_id == source_page_id)
        count_query = count_query.where(ExtractionCandidate.source_page_id == source_page_id)
    if validation_status:
        condition = ExtractionCandidate.validation_results.contains([{"status": validation_status}])
        query = query.where(condition)
        count_query = count_query.where(condition)
    conflict_exists = select(ExtractionConflictCandidate.candidate_id).join(ExtractionConflict, ExtractionConflict.id == ExtractionConflictCandidate.conflict_id).where(ExtractionConflictCandidate.candidate_id == ExtractionCandidate.id)
    if conflict_status == "NONE":
        query = query.where(~conflict_exists.exists())
        count_query = count_query.where(~conflict_exists.exists())
    elif conflict_status:
        condition = conflict_exists.where(ExtractionConflict.status == conflict_status).exists()
        query = query.where(condition)
        count_query = count_query.where(condition)
    columns = {"created_at": ExtractionCandidate.created_at, "candidate_type": ExtractionCandidate.candidate_type, "classification": ExtractionCandidate.classification, "confidence": ExtractionCandidate.extraction_confidence}
    order = columns[sort_by].asc() if sort_order == "asc" else columns[sort_by].desc()
    total = await session.scalar(count_query) or 0
    result = await session.execute(query.distinct().order_by(order, ExtractionCandidate.id).offset((page - 1) * page_size).limit(page_size))
    candidates = list(result.scalars().all())
    items = []
    for candidate in candidates:
        detail = await _candidate_detail(session, candidate.id)
        items.append({**detail, "validation_results": candidate.validation_results, "verification_status": candidate.verification_status, "classification": candidate.classification, "classification_status": candidate.classification_status, "mapping_status": candidate.mapping_status})
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@router.get("/verification/candidates/{candidate_id}")
async def get_verification_candidate(candidate_id: uuid.UUID, session: AsyncSession = Depends(get_db), actor: User = Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))) -> dict:
    return await _candidate_detail(session, candidate_id)


@router.get("/verification/candidates/{candidate_id}/provenance")
async def candidate_provenance(candidate_id: uuid.UUID, session: AsyncSession = Depends(get_db), actor: User = Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))) -> dict:
    detail = await _candidate_detail(session, candidate_id)
    events = (await session.execute(select(VerificationEvent).where(VerificationEvent.candidate_id == candidate_id).order_by(VerificationEvent.created_at, VerificationEvent.id))).scalars().all()
    record = await session.scalar(select(CanonicalRecord).where(CanonicalRecord.candidate_id == candidate_id))
    detail["verification_events"] = events
    detail["canonical_record"] = record
    return jsonable_encoder(detail)


@router.post("/verification/candidates/{candidate_id}/claim")
async def claim_verification_candidate(candidate_id: uuid.UUID, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        candidate = await claim_candidate(session, candidate_id, actor.id)
        return {"candidate_id": candidate.id, "verification_status": candidate.verification_status, "classification": candidate.classification, "classification_status": candidate.classification_status, "classification_confidence": candidate.classification_confidence, "classification_rule": candidate.classification_rule, "mapping_status": candidate.mapping_status}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/verification/candidates/{candidate_id}/approve")
async def approve_verification_candidate(candidate_id: uuid.UUID, request: ReviewRequest, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        record = await approve_candidate(session, candidate_id, actor_id=actor.id, reason=request.reason, canonical_entity_id=request.canonical_entity_id)
        return {"canonical_record_id": record.id, "candidate_id": record.candidate_id, "canonical_entity_id": record.canonical_entity_id, "verification_status": "VERIFIED"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/verification/candidates/{candidate_id}/reject")
async def reject_verification_candidate(candidate_id: uuid.UUID, request: ReviewRequest, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        candidate = await reject_candidate(session, candidate_id, actor_id=actor.id, reason=request.reason)
        return {"candidate_id": candidate.id, "verification_status": candidate.verification_status}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/verification/candidates/{candidate_id}/edit-and-approve")
async def edit_and_approve_candidate(candidate_id: uuid.UUID, request: EditApproveRequest, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        record = await approve_candidate(session, candidate_id, actor_id=actor.id, reason=request.reason, edited_value=request.edited_value, canonical_entity_id=request.canonical_entity_id)
        return {"canonical_record_id": record.id, "candidate_id": record.candidate_id, "canonical_entity_id": record.canonical_entity_id, "verification_status": "VERIFIED", "accepted_value": record.accepted_value}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/verification/candidates/{candidate_id}/mark-unresolved")
async def mark_candidate_unresolved(candidate_id: uuid.UUID, request: ReviewRequest, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        candidate = await unresolved_candidate(session, candidate_id, actor_id=actor.id, reason=request.reason)
        return {"candidate_id": candidate.id, "verification_status": candidate.verification_status}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/verification/conflicts")
async def list_conflicts(actor: Reader, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), status: str | None = None, session: AsyncSession = Depends(get_db)) -> dict:
    query = select(ExtractionConflict)
    count_query = select(func.count()).select_from(ExtractionConflict)
    if status:
        query, count_query = query.where(ExtractionConflict.status == status), count_query.where(ExtractionConflict.status == status)
    total = await session.scalar(count_query) or 0
    rows = (await session.execute(query.order_by(ExtractionConflict.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return jsonable_encoder({"items": [{"conflict": item, "candidate_ids": (await session.execute(select(ExtractionConflictCandidate.candidate_id).where(ExtractionConflictCandidate.conflict_id == item.id))).scalars().all()} for item in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/verification/conflicts/{conflict_id}")
async def get_conflict(conflict_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> dict:
    conflict = await session.get(ExtractionConflict, conflict_id)
    if conflict is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    candidate_ids = (await session.execute(select(ExtractionConflictCandidate.candidate_id).where(ExtractionConflictCandidate.conflict_id == conflict_id))).scalars().all()
    references = [await _candidate_detail(session, candidate_id) for candidate_id in candidate_ids]
    return jsonable_encoder({"conflict": conflict, "candidates": references})


@router.post("/verification/conflicts/{conflict_id}/resolve")
async def resolve_verification_conflict(conflict_id: uuid.UUID, request: ConflictResolutionRequest, actor: User = Depends(require_roles("ADMIN", "VERIFIER")), session: AsyncSession = Depends(get_db)) -> dict:
    try:
        conflict = await resolve_conflict(session, conflict_id, status=request.status, resolution=request.resolution, actor_id=actor.id)
        return {"conflict_id": conflict.id, "status": conflict.status, "resolution": conflict.resolution}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/canonical/entities")
async def list_canonical_entities(actor: Reader, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), entity_type: str | None = None, session: AsyncSession = Depends(get_db)) -> dict:
    query = select(CanonicalEntity)
    count_query = select(func.count()).select_from(CanonicalEntity)
    if entity_type:
        query, count_query = query.where(CanonicalEntity.entity_type == entity_type.upper()), count_query.where(CanonicalEntity.entity_type == entity_type.upper())
    total = await session.scalar(count_query) or 0
    items = (await session.execute(query.order_by(CanonicalEntity.canonical_name).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return jsonable_encoder({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/canonical/entities/{entity_id}")
async def get_canonical_entity(entity_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> dict:
    entity = await session.get(CanonicalEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Canonical entity not found")
    records = (await session.execute(select(CanonicalRecord).where(CanonicalRecord.canonical_entity_id == entity_id).order_by(CanonicalRecord.created_at.desc()))).scalars().all()
    return jsonable_encoder({"entity": entity, "records": records})
