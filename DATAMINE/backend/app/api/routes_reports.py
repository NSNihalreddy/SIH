from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_storage_service
from app.core.auth import require_roles
from app.db.session import get_db
from app.models.identity import AuditLog, User
from app.models.reports import Report, ReportArtifact
from app.services.reporting import validate_report
from app.storage.base import StorageError
from app.workers.tasks import generate_report_task

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Generator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


class ReportType(str, Enum):
    PRODUCTION = "PRODUCTION"
    DOCUMENT_INTELLIGENCE = "DOCUMENT_INTELLIGENCE"
    MULTI_DOCUMENT_COMPARISON = "MULTI_DOCUMENT_COMPARISON"
    EXECUTIVE_SUMMARY = "EXECUTIVE_SUMMARY"


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_type: ReportType
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=2000)
    year: int | None = Field(default=None, ge=1800, le=2200)
    year_from: int | None = Field(default=None, ge=1800, le=2200)
    year_to: int | None = Field(default=None, ge=1800, le=2200)
    state: str | None = Field(default=None, max_length=120)
    commodity: str | None = Field(default=None, max_length=120)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def parameters_match_type(self):
        if not self.title.strip():
            raise ValueError("Report title must contain non-whitespace text")
        if self.report_type == ReportType.PRODUCTION and self.document_ids:
            raise ValueError("Production report parameters cannot select documents")
        if self.report_type != ReportType.PRODUCTION and (self.year is not None or self.year_from is not None or self.year_to is not None or self.state or self.commodity):
            raise ValueError("Production filters are accepted only for production reports")
        if self.year is not None and (self.year_from is not None or self.year_to is not None):
            raise ValueError("Use either a specific year or a year range")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must be less than or equal to year_to")
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("Duplicate document IDs are not allowed")
        return self


def _serialize(report: Report) -> dict:
    return {"report_id": str(report.id), "report_type": report.report_type, "title": report.title,
        "description": report.description, "status": report.status, "validation_status": report.validation_status,
        "requested_by": str(report.requested_by) if report.requested_by else None, "parameters": report.parameters,
        "source_document_ids": report.source_document_ids, "source_version_ids": report.source_version_ids,
        "sections": report.sections, "evidence_count": len(report.evidence_references or []),
        "provenance": report.provenance, "validation": report.validation, "error_message": report.error_message,
        "artifacts": [{"artifact_type": a.artifact_type, "filename": a.filename, "mime_type": a.mime_type,
            "size_bytes": a.size_bytes, "checksum_sha256": a.checksum_sha256} for a in report.artifacts],
        "created_at": report.created_at, "completed_at": report.completed_at}


@router.post("", status_code=202)
async def create_report(request: ReportRequest, actor: Generator,
                        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
                        session: AsyncSession = Depends(get_db)):
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 100):
        raise HTTPException(422, "Idempotency-Key must contain 1 to 100 characters")
    if idempotency_key:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "report:" + idempotency_key})
        existing = await session.scalar(select(Report).where(Report.idempotency_key == idempotency_key))
        if existing:
            return {"report_id": str(existing.id), "status": existing.status, "submitted": False, "idempotent_reuse": True}
    parameters = {key: value for key, value in {"year": request.year, "year_from": request.year_from, "year_to": request.year_to, "state": request.state,
        "commodity": request.commodity, "document_ids": [str(x) for x in request.document_ids]}.items() if value is not None and value != []}
    report = Report(report_type=request.report_type.value, title=request.title.strip(), description=request.description,
        requested_by=actor.id, idempotency_key=idempotency_key, parameters=parameters)
    session.add(report)
    await session.flush()
    session.add(AuditLog(actor_id=actor.id, action="REPORT_CREATED", entity_type="REPORT", entity_id=report.id,
        details={"report_type": report.report_type, "parameters": parameters}, source="reports_api"))
    await session.commit()
        try:
        generate_report_task.run(str(report.id))
    except Exception as exc:
        report = await session.get(Report, report.id)
        report.status = "FAILED"
        report.error_message = str(exc)
        report.completed_at = datetime.now(timezone.utc)
        await session.commit()

        session.add(
            AuditLog(
                actor_id=actor.id,
                action="REPORT_GENERATION_FAILED",
                entity_type="REPORT",
                entity_id=report.id,
                details={"error_type": type(exc).__name__},
                source="reports_api",
            )
        )
        await session.commit()

        raise HTTPException(
            500,
            f"Report generation failed: {exc}",
        ) from exc

    return {
        "report_id": str(report.id),
        "status": report.status,
        "submitted": True,
    }


@router.get("")
async def list_reports(actor: Reader, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                       session: AsyncSession = Depends(get_db)):
    query = select(Report).options(selectinload(Report.artifacts)).order_by(Report.created_at.desc())
    total = await session.scalar(select(func.count(Report.id)))
    reports = (await session.execute(query.offset((page-1)*page_size).limit(page_size))).scalars().unique().all()
    session.add(AuditLog(actor_id=actor.id, action="REPORT_LIST_READ", entity_type="REPORT", details={"page": page}, source="reports_api")); await session.commit()
    return {"items": [_serialize(item) for item in reports], "total": total or 0, "page": page, "page_size": page_size}


async def _get_report(session: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await session.scalar(select(Report).where(Report.id == report_id).options(selectinload(Report.artifacts)))
    if report is None:
        raise HTTPException(404, "Report not found")
    return report


@router.get("/{report_id}")
async def report_detail(report_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    report = await _get_report(session, report_id)
    session.add(AuditLog(actor_id=actor.id, action="REPORT_READ", entity_type="REPORT", entity_id=report.id, source="reports_api")); await session.commit()
    return _serialize(report)


@router.get("/{report_id}/status")
async def report_status(report_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    report = await _get_report(session, report_id)
    session.add(AuditLog(actor_id=actor.id, action="REPORT_STATUS_READ", entity_type="REPORT", entity_id=report.id, source="reports_api")); await session.commit()
    return {"report_id": str(report.id), "status": report.status, "validation_status": report.validation_status,
        "error_message": report.error_message, "created_at": report.created_at, "completed_at": report.completed_at}


@router.get("/{report_id}/evidence")
async def report_evidence(report_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    report = await _get_report(session, report_id)
    session.add(AuditLog(actor_id=actor.id, action="REPORT_EVIDENCE_READ", entity_type="REPORT", entity_id=report.id,
        details={"evidence_count": len(report.evidence_references or [])}, source="reports_api")); await session.commit()
    return {"report_id": str(report.id), "evidence": report.evidence_references or [], "provenance": report.provenance or {}}


@router.get("/{report_id}/download")
async def download_report(report_id: uuid.UUID, actor: Reader, format: str = Query("PDF", pattern="^(PDF|DOCX|XLSX)$"),
                          session: AsyncSession = Depends(get_db), storage=Depends(get_storage_service)):
    report = await _get_report(session, report_id)
    if report.status != "COMPLETED":
        raise HTTPException(409, "Report artifact is not ready")
    artifact = await session.scalar(select(ReportArtifact).where(ReportArtifact.report_id == report.id,
        ReportArtifact.artifact_type == format.upper()))
    if not artifact:
        raise HTTPException(404, "Requested report format is unavailable")
    try:
        with storage.download_file(artifact.storage_key) as stream:
            content = stream.read()
    except StorageError as exc:
        raise HTTPException(503, "Report artifact storage is unavailable") from exc
    session.add(AuditLog(actor_id=actor.id, action="REPORT_ARTIFACT_DOWNLOADED", entity_type="REPORT", entity_id=report.id,
        details={"artifact_id": str(artifact.id), "artifact_type": artifact.artifact_type}, source="reports_api")); await session.commit()
    return Response(content=content, media_type=artifact.mime_type, headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"', "Content-Length": str(len(content))})
