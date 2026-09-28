import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder

from app.core.auth import get_current_user, require_roles
from app.db.session import get_db
from app.models.identity import AuditLog, User
from app.models.trusted import (TrustedMine, TrustedProject, TrustedBorehole, CoalBlock, GeologicalFormation, Seam,
    TrustedGeologicalMeasurement, TrustedProductionRecord, TrustedCoordinate, CanonicalValueHistory, CanonicalDataConflict)
from app.services.canonicalization import canonicalize_candidate

router = APIRouter(tags=["trusted canonical data"])
Reviewer = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER"))]
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]


class BatchRequest(BaseModel):
    candidate_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=1000)


class ConflictResolution(BaseModel):
    status: str = Field(pattern="^(UNDER_REVIEW|RESOLVED|DISMISSED)$")
    resolution: str = Field(min_length=1, max_length=2000)


@router.post("/api/v1/canonicalization/candidates/{candidate_id}")
async def canonicalize_one(candidate_id: uuid.UUID, actor: Reviewer, session: AsyncSession = Depends(get_db)):
    try:
        return await canonicalize_candidate(session, candidate_id, actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/v1/canonicalization/batch")
async def canonicalize_batch(request: BatchRequest, actor: Reviewer, session: AsyncSession = Depends(get_db)):
    results = []
    for candidate_id in dict.fromkeys(request.candidate_ids):
        try:
            results.append(await canonicalize_candidate(session, candidate_id, actor, request.reason))
        except (LookupError, ValueError, PermissionError) as exc:
            results.append({"candidate_id": candidate_id, "error": str(exc)})
    return {"items": results, "processed": len(results)}


@router.get("/api/v1/canonicalization/status")
async def canonicalization_status(actor: Reader, session: AsyncSession = Depends(get_db)):
    from app.models.extraction import ExtractionCandidate
    statuses = (await session.execute(select(ExtractionCandidate.verification_status, func.count()).group_by(ExtractionCandidate.verification_status))).all()
    tables = [TrustedMine, TrustedProject, TrustedBorehole, CoalBlock, GeologicalFormation, Seam, TrustedGeologicalMeasurement, TrustedProductionRecord, TrustedCoordinate]
    counts = {model.__tablename__: await session.scalar(select(func.count()).select_from(model)) for model in tables}
    return {"candidate_status_counts": dict(statuses), "trusted_record_counts": counts}


async def _list(model, session, page, page_size, q, sort_by, sort_order, status_filter=None):
    allowed = {column.key: getattr(model, column.key) for column in model.__table__.columns if column.key in {"canonical_name", "normalized_name", "created_at", "domain_code", "reporting_period", "measurement_type"}}
    if sort_by is None:
        sort_by = "canonical_name" if "canonical_name" in allowed else "reporting_period" if "reporting_period" in allowed else "measurement_type"
    if sort_by not in allowed:
        raise HTTPException(422, "Unsupported sort field")
    query, count = select(model), select(func.count()).select_from(model)
    if status_filter and hasattr(model, "status"):
        query, count = query.where(model.status == status_filter), count.where(model.status == status_filter)
    if q and hasattr(model, "canonical_name"):
        query, count = query.where(model.canonical_name.ilike(f"%{q}%")), count.where(model.canonical_name.ilike(f"%{q}%"))
    order = allowed[sort_by].asc() if sort_order == "asc" else allowed[sort_by].desc()
    total = await session.scalar(count) or 0
    rows = (await session.execute(query.order_by(order, model.id).offset((page-1)*page_size).limit(page_size))).scalars().all()
    return jsonable_encoder({"items": rows, "total": total, "page": page, "page_size": page_size, "pages": (total+page_size-1)//page_size})


def _list_route(path, model):
    async def endpoint(actor: Reader, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), q: str | None = None,
        sort_by: str | None = None, sort_order: str = Query("asc", pattern="^(asc|desc)$"), status: str | None = None,
        session: AsyncSession = Depends(get_db)):
        return await _list(model, session, page, page_size, q, sort_by, sort_order, status)
    endpoint.__name__ = "list_" + path.replace("/", "_")
    router.add_api_route(path, endpoint, methods=["GET"])


for path, model in [("/api/v1/canonical/mines", TrustedMine), ("/api/v1/canonical/projects", TrustedProject),
    ("/api/v1/canonical/boreholes", TrustedBorehole), ("/api/v1/canonical/coal-blocks", CoalBlock),
    ("/api/v1/canonical/geology", GeologicalFormation), ("/api/v1/canonical/production", TrustedProductionRecord)]:
    _list_route(path, model)


def _detail_route(path, model):
    async def endpoint(entity_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
        item = await session.get(model, entity_id)
        if item is None:
            raise HTTPException(404, "Canonical record not found")
        return jsonable_encoder(item)
    endpoint.__name__ = "detail_" + path.replace("/", "_")
    router.add_api_route(path, endpoint, methods=["GET"])


for path, model in [("/api/v1/canonical/mines/{entity_id}", TrustedMine),
    ("/api/v1/canonical/projects/{entity_id}", TrustedProject),
    ("/api/v1/canonical/boreholes/{entity_id}", TrustedBorehole)]:
    _detail_route(path, model)


@router.get("/api/v1/canonical/{entity_type}/{entity_id}/provenance")
async def provenance(entity_type: str, entity_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(CanonicalValueHistory).where(CanonicalValueHistory.entity_type == entity_type.upper(), CanonicalValueHistory.entity_id == entity_id).order_by(CanonicalValueHistory.created_at, CanonicalValueHistory.id))).scalars().all()
    if not rows:
        raise HTTPException(404, "Provenance not found")
    result = []
    for row in rows:
        from app.models.canonical import CanonicalRecord
        from app.models.documents import Document, DocumentVersion, DocumentPage
        record = await session.get(CanonicalRecord, row.verification_id)
        doc = await session.get(Document, record.source_document_id) if record else None
        version = await session.get(DocumentVersion, record.source_version_id) if record else None
        page = await session.get(DocumentPage, record.source_page_id) if record and record.source_page_id else None
        result.append({"field": row.field_name, "original_value": row.original_value, "accepted_value": row.accepted_value, "normalized_value": row.normalized_value,
            "candidate_id": row.source_candidate_id, "verification_id": row.verification_id, "verifier_id": row.verifier_id,
            "verified_at": row.created_at, "document_id": doc.id if doc else None, "document_checksum": doc.sha256_checksum if doc else None,
            "document_version_id": version.id if version else None, "version_checksum": version.sha256_checksum if version else None,
            "page_id": page.id if page else None, "page_number": page.page_number if page else None,
            "source_content_id": record.source_content_id if record else None, "source_table_id": record.source_table_id if record else None, "source_cell_id": record.source_cell_id if record else None})
    return {"entity_type": entity_type, "entity_id": entity_id, "items": result}


@router.get("/api/v1/conflicts")
async def list_canonical_conflicts(actor: Reader, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), status: str | None = None, session: AsyncSession = Depends(get_db)):
    query, count = select(CanonicalDataConflict), select(func.count()).select_from(CanonicalDataConflict)
    if status:
        query, count = query.where(CanonicalDataConflict.status == status), count.where(CanonicalDataConflict.status == status)
    total = await session.scalar(count) or 0
    items = (await session.execute(query.order_by(CanonicalDataConflict.created_at.desc(), CanonicalDataConflict.id).offset((page-1)*page_size).limit(page_size))).scalars().all()
    return jsonable_encoder({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/api/v1/conflicts/{conflict_id}")
async def get_canonical_conflict(conflict_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    row = await session.get(CanonicalDataConflict, conflict_id)
    if not row: raise HTTPException(404, "Conflict not found")
    return jsonable_encoder(row)


@router.post("/api/v1/conflicts/{conflict_id}/resolve")
async def resolve_canonical_conflict(conflict_id: uuid.UUID, request: ConflictResolution, actor: Reviewer, session: AsyncSession = Depends(get_db)):
    row = await session.scalar(select(CanonicalDataConflict).where(CanonicalDataConflict.id == conflict_id).with_for_update())
    if not row: raise HTTPException(404, "Conflict not found")
    if row.status not in {"OPEN", "UNDER_REVIEW"}: raise HTTPException(409, "Conflict is already closed")
    from datetime import datetime, timezone
    row.status, row.resolution = request.status, request.resolution
    row.resolved_by = actor.id if request.status in {"RESOLVED", "DISMISSED"} else None
    row.resolved_at = datetime.now(timezone.utc) if row.resolved_by else None
    session.add(AuditLog(actor_id=actor.id, action="CONFLICT_RESOLVE" if row.resolved_by else "CONFLICT_REVIEW", entity_type=row.entity_type, entity_id=row.entity_id, details={"conflict_id": str(row.id), "status": row.status, "resolution": request.resolution}))
    await session.commit()
    return {"conflict_id": row.id, "status": row.status, "resolved_by": actor.id}
