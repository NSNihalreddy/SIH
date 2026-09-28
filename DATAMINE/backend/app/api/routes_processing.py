import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import cast, select, text, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import require_roles
from app.db.session import get_db
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.identity import User
from app.models.intelligence import ExtractedTable
from app.models.processing import ProcessingJob, ProcessingStage
from app.schemas.processing import DocumentPageResponse, ProcessingJobResponse, ProcessingStatusResponse
from app.workers.tasks import process_document_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/processing", tags=["processing"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Operator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]
ACTIVE_STATUSES = ("QUEUED", "PROCESSING", "PARSING", "OCR", "TABLE_EXTRACTION", "NORMALIZATION", "VALIDATION", "VERIFICATION_PENDING")


@router.post("/{document_id}/process", response_model=ProcessingJobResponse, status_code=202)
async def submit_processing(document_id: uuid.UUID, actor: Operator, session: AsyncSession = Depends(get_db)) -> ProcessingJob:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    version_result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=409, detail="Document has no version to process")

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:version_id, 2))"),
        {"version_id": str(version.id)},
    )
    active_result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.document_version_id == version.id, ProcessingJob.status.in_(ACTIVE_STATUSES))
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )
    active_job = active_result.scalar_one_or_none()
    if active_job is not None:
        active_job_id = active_job.id
        active_job_status = active_job.status
        await session.rollback()
        raise HTTPException(status_code=409, detail={"message": "A processing job is already active", "job_id": str(active_job_id), "status": active_job_status})

    now = datetime.now(timezone.utc)
    job = ProcessingJob(
        id=uuid.uuid4(),
        document_version_id=version.id,
        status="QUEUED",
        created_at=now,
        updated_at=now,
    )
    queued_stage = ProcessingStage(job_id=job.id, stage="QUEUED", status="QUEUED", created_at=now)
    session.add_all([job, queued_stage])
    await session.commit()
    await session.refresh(job)
    job_id = job.id
    queued_stage_id = queued_stage.id

    try:
        process_document_task(str(job_id))
    except Exception as exc:
        await session.rollback()
        failed_at = datetime.now(timezone.utc)
        job = await session.get(ProcessingJob, job_id)
        queued_stage = await session.get(ProcessingStage, queued_stage_id)
        if job is not None:
            job.status = "FAILED"
            job.error_message = f"Could not submit processing task ({type(exc).__name__})"
            job.completed_at = failed_at
        if queued_stage is not None:
            queued_stage.status = "FAILED"
            queued_stage.error_message = "Celery task submission failed"
            queued_stage.completed_at = failed_at
        await session.commit()
        logger.error("Celery submission failed job_id=%s error_type=%s", job_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={"message": "Processing queue is unavailable; the job was marked FAILED", "job_id": str(job_id), "status": "FAILED"},
        ) from exc

    logger.info("Processing job queued job_id=%s document_id=%s", job_id, document_id)
    return job


@router.get("/{document_id}/processing-status", response_model=ProcessingStatusResponse)
async def get_processing_status(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> ProcessingStatusResponse:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await session.execute(
        select(ProcessingJob)
        .join(DocumentVersion, ProcessingJob.document_version_id == DocumentVersion.id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="No processing job found")
    stages_result = await session.execute(
        select(ProcessingStage).where(ProcessingStage.job_id == job.id).order_by(ProcessingStage.created_at, ProcessingStage.id)
    )
    return ProcessingStatusResponse(job=job, stages=list(stages_result.scalars().all()))


@router.get("/{document_id}/pages", response_model=list[DocumentPageResponse])
async def get_document_pages(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> list[DocumentPage]:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    version_result = await session.execute(
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    version_id = version_result.scalar_one_or_none()
    if version_id is None:
        return []
    result = await session.execute(
        select(DocumentPage)
        .options(selectinload(DocumentPage.contents), selectinload(DocumentPage.tables).selectinload(ExtractedTable.cells))
        .where(DocumentPage.document_version_id == version_id)
        .order_by(
            DocumentPage.page_number.asc().nullslast(),
            cast(DocumentPage.page_metadata["sheet_index"].astext, Integer).asc().nullslast(),
            DocumentPage.created_at,
        )
    )
    return list(result.scalars().all())
