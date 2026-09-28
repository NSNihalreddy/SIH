import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_storage_service
from app.core.auth import require_roles
from app.core.config import MAX_UPLOAD_SIZE_BYTES
from app.db.session import get_db
from app.models.documents import Document, DocumentVersion
from app.models.identity import User
from app.schemas.documents import DocumentResponse, DocumentVersionResponse
from app.services.document_service import (
    DatabaseWriteError,
    DuplicateDocumentUpload,
    EmptyUpload,
    UploadTooLarge,
    create_document_upload,
)
from app.services.document_validation import UnsupportedDocumentType, validate_upload
from app.storage.base import StorageError, StorageNotFoundError, StorageService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Uploader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    actor: Uploader,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> Document:
    try:
        if file.size is not None and file.size > MAX_UPLOAD_SIZE_BYTES:
            raise UploadTooLarge("Upload exceeds the configured size limit")
        if file.size == 0:
            raise EmptyUpload("Empty files cannot be uploaded")
        filename, document_type, mime_type = validate_upload(file)
        document, _ = await create_document_upload(
            session,
            storage,
            file.file,
            filename=filename,
            document_type=document_type,
            mime_type=mime_type,
        )
        return document
    except UnsupportedDocumentType as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit") from exc
    except EmptyUpload as exc:
        raise HTTPException(status_code=400, detail="Empty files cannot be uploaded") from exc
    except DuplicateDocumentUpload as exc:
        raise HTTPException(status_code=409, detail={"message": "This document was already uploaded", "document_id": str(exc.document_id)}) from exc
    except DatabaseWriteError as exc:
        logger.exception("Could not persist uploaded document metadata")
        raise HTTPException(status_code=500, detail="Could not save uploaded document") from exc
    except StorageError as exc:
        logger.exception("Document storage operation failed")
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from exc
    finally:
        await file.close()


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    actor: Reader,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> list[Document]:
    result = await session.execute(select(Document).order_by(Document.created_at.desc()).offset(offset).limit(limit))
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}/versions", response_model=list[DocumentVersionResponse])
async def list_document_versions(document_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)) -> list[DocumentVersion]:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    return list(result.scalars().all())


@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    actor: Reader,
    session: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> StreamingResponse:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Document version not found")
    try:
        stored_file = await run_in_threadpool(storage.download_file, version.storage_key)
    except StorageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Stored document file not found") from exc
    except StorageError as exc:
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from exc

    async def stream() -> AsyncIterator[bytes]:
        try:
            while chunk := await run_in_threadpool(stored_file.read, 1024 * 1024):
                yield chunk
        finally:
            await run_in_threadpool(stored_file.close)

    return StreamingResponse(
        stream(),
        media_type=document.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{document.original_filename}"'},
    )
