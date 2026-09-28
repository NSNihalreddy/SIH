from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_roles
from app.db.session import get_db
from app.models.canonical import VerificationEvent
from app.models.identity import AuditLog, User

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]


@router.get("")
async def list_audit_events(
    actor: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Read the existing append-only audit and verification event streams."""
    audit_events = select(
        AuditLog.id.label("id"), AuditLog.actor_id.label("actor_id"),
        AuditLog.action.label("action"), AuditLog.entity_type.label("entity_type"),
        AuditLog.entity_id.label("entity_id"), AuditLog.details.label("details"),
        AuditLog.source.label("source"), AuditLog.created_at.label("created_at"),
    )
    verification_events = select(
        VerificationEvent.id.label("id"), VerificationEvent.actor_id.label("actor_id"),
        VerificationEvent.action.label("action"), literal("EXTRACTION_CANDIDATE").label("entity_type"),
        VerificationEvent.candidate_id.label("entity_id"),
        cast(func.jsonb_build_object(
            "previous_state", VerificationEvent.previous_state,
            "new_state", VerificationEvent.new_state,
            "previous_value", VerificationEvent.previous_value,
            "new_value", VerificationEvent.new_value,
            "reason", VerificationEvent.reason,
        ), JSONB).label("details"),
        literal("verification_workflow").label("source"),
        VerificationEvent.created_at.label("created_at"),
    )
    events = union_all(audit_events, verification_events).subquery("audit_events")
    total = int(await session.scalar(select(func.count()).select_from(events)) or 0)
    rows = (await session.execute(
        select(events).order_by(events.c.created_at.desc(), events.c.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "page": page,
        "page_size": page_size, "pages": (total + page_size - 1) // page_size}
