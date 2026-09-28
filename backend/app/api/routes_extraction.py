import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_roles
from app.db.session import get_db
from app.models.documents import Document, DocumentVersion
from app.models.extraction import ExtractionCandidate
from app.models.identity import User
from app.models.processing import ProcessingJob
from app.schemas.extraction import ExtractionCandidateResponse, ExtractionRunResponse
from app.services.structured_extraction import extract_document_version

router = APIRouter(prefix="/api/v1", tags=["structured-extraction"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Operator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


@router.post("/documents/{document_id}/extract", response_model=ExtractionRunResponse, status_code=202)
async def run_structured_extraction(document_id: uuid.UUID, actor: Operator, session: AsyncSession = Depends(get_db)) -> dict:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    version_result = await session.execute(select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_number.desc()).limit(1))
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=409, detail="Document has no version")
    completed = await session.execute(select(ProcessingJob.id).where(ProcessingJob.document_version_id == version.id, ProcessingJob.status == "COMPLETED").limit(1))
    if completed.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="Step 5 processing must complete before structured extraction")
    try:
        return await extract_document_version(session, document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/documents/{document_id}/extractions", response_model=list[ExtractionCandidateResponse])
async def get_document_extractions(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> list[ExtractionCandidate]:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id).order_by(ExtractionCandidate.created_at, ExtractionCandidate.id))
    return list(result.scalars().all())


@router.get("/documents/{document_id}/pages/{page_id}/extractions", response_model=list[ExtractionCandidateResponse])
async def get_page_extractions(document_id: uuid.UUID, page_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> list[ExtractionCandidate]:
    result = await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id, ExtractionCandidate.source_page_id == page_id).order_by(ExtractionCandidate.created_at, ExtractionCandidate.id))
    return list(result.scalars().all())


@router.get("/extractions/{candidate_id}", response_model=ExtractionCandidateResponse)
async def get_extraction_candidate(candidate_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> ExtractionCandidate:
    candidate = await session.get(ExtractionCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Extraction candidate not found")
    return candidate


@router.get("/documents/{document_id}/validation-results")
async def get_document_validation_results(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> list[dict]:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id).order_by(ExtractionCandidate.created_at))
    return [{"candidate_id": item.id, "candidate_type": item.candidate_type, "source_page_id": item.source_page_id, "results": item.validation_results} for item in result.scalars().all()]
