import hashlib
import logging
import uuid
from typing import BinaryIO

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MAX_UPLOAD_SIZE_BYTES
from app.models.documents import Document, DocumentVersion
from app.storage.base import StorageError, StorageService

logger = logging.getLogger(__name__)


class UploadTooLarge(StorageError):
    pass


class EmptyUpload(ValueError):
    pass


class DuplicateDocumentUpload(Exception):
    def __init__(self, document_id: uuid.UUID):
        self.document_id = document_id
        super().__init__("A document with this content already exists")


class DatabaseWriteError(Exception):
    pass


class HashingLimitedReader:
    def __init__(self, source: BinaryIO, max_bytes: int):
        self.source = source
        self.max_bytes = max_bytes
        self.size = 0
        self.sha256 = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(size)
        if chunk:
            self.size += len(chunk)
            if self.size > self.max_bytes:
                raise UploadTooLarge("Upload exceeds the configured size limit")
            self.sha256.update(chunk)
        return chunk


async def create_document_upload(
    session: AsyncSession,
    storage: StorageService,
    upload: BinaryIO,
    *,
    filename: str,
    document_type: str,
    mime_type: str,
    created_by: uuid.UUID | None = None,
) -> tuple[Document, DocumentVersion]:
    upload_size = getattr(upload, "size", None)
    if upload_size is not None and upload_size > MAX_UPLOAD_SIZE_BYTES:
        raise UploadTooLarge("Upload exceeds the configured size limit")
    if upload_size == 0:
        raise EmptyUpload("Empty files cannot be uploaded")

    document_id = uuid.uuid4()
    version_id = uuid.uuid4()
    storage_key = f"documents/{document_id}/versions/1/{filename}"
    reader = HashingLimitedReader(upload, MAX_UPLOAD_SIZE_BYTES)
    try:
        await run_in_threadpool(storage.upload_file, reader, storage_key, mime_type)
    except UploadTooLarge:
        raise
    except StorageError:
        raise
    except Exception as exc:
        raise StorageError("Could not store the uploaded file") from exc

    if reader.size == 0:
        await run_in_threadpool(storage.delete_file, storage_key)
        raise EmptyUpload("Empty files cannot be uploaded")

    checksum = reader.sha256.hexdigest()
    try:
        # Serialize same-checksum uploads so concurrent requests cannot create
        # duplicate document/version rows in PostgreSQL.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:checksum, 0))"),
            {"checksum": checksum},
        )
        existing = await session.execute(select(Document).where(Document.sha256_checksum == checksum))
        duplicate = existing.scalar_one_or_none()
        if duplicate is not None:
            duplicate_id = duplicate.id
            await session.rollback()
            await run_in_threadpool(storage.delete_file, storage_key)
            raise DuplicateDocumentUpload(duplicate_id)

        document = Document(
            id=document_id,
            original_filename=filename,
            document_type=document_type,
            mime_type=mime_type,
            file_size=reader.size,
            storage_key=storage_key,
            sha256_checksum=checksum,
            status="UPLOADED",
            created_by=created_by,
        )
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=1,
            storage_key=storage_key,
            sha256_checksum=checksum,
            file_size=reader.size,
            created_by=created_by,
        )
        session.add(document)
        session.add(version)
        await session.commit()
        return document, version
    except DuplicateDocumentUpload:
        raise
    except Exception as exc:
        await session.rollback()
        try:
            await run_in_threadpool(storage.delete_file, storage_key)
        except StorageError:
            logger.exception("Could not clean up storage object after database failure")
        raise DatabaseWriteError("Could not save uploaded document metadata") from exc
