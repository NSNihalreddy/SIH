import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import (
    SYNC_DATABASE_URL,
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    VECTOR_EMBEDDING_DIMENSION,
)
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.intelligence import ExtractedContent, ExtractedTable, ExtractedTableCell
from app.models.processing import ProcessingJob, ProcessingStage
from app.models.rag import IndexingJob
from app.processors.base import ProcessingResult
from app.processors.router import ProcessorRouter
from app.storage.base import StorageService
from app.workers.search_index import run_indexing_task

logger = logging.getLogger(__name__)


class ProcessingFailure(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _transition(session: Session, job: ProcessingJob, stage_name: str) -> None:
    previous = session.execute(
        select(ProcessingStage)
        .where(
            ProcessingStage.job_id == job.id,
            ProcessingStage.status == "PROCESSING",
        )
        .order_by(ProcessingStage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    now = _utcnow()

    if previous is not None:
        previous.status = "COMPLETED"
        previous.completed_at = now

        logger.info(
            "Processing job stage completed job_id=%s stage=%s",
            job.id,
            previous.stage,
        )

    job.status = stage_name

    if stage_name != "COMPLETED":
        session.add(
            ProcessingStage(
                job_id=job.id,
                stage=stage_name,
                status="PROCESSING",
                started_at=now,
            )
        )

        logger.info(
            "Processing job stage started job_id=%s stage=%s",
            job.id,
            stage_name,
        )

    else:
        session.add(
            ProcessingStage(
                job_id=job.id,
                stage="COMPLETED",
                status="COMPLETED",
                started_at=now,
                completed_at=now,
            )
        )

        job.completed_at = now

        logger.info(
            "Processing job completed job_id=%s",
            job.id,
        )

    session.commit()


def _persist_result(
    session: Session,
    job: ProcessingJob,
    result: ProcessingResult,
) -> None:

    if not result.pages:
        raise ProcessingFailure(
            "Processor returned no document pages or source units"
        )

    session.execute(
        delete(DocumentPage)
        .where(
            DocumentPage.document_version_id
            == job.document_version_id
        )
        .execution_options(
            synchronize_session=False
        )
    )

    session.flush()

    for page_result in result.pages:

        page = DocumentPage(
            id=uuid4(),
            document_version_id=job.document_version_id,
            page_number=page_result.page_number,
            text=page_result.text,
            page_metadata=page_result.metadata,
        )

        session.add(page)
        session.flush()

        for content_result in page_result.contents:

            session.add(
                ExtractedContent(
                    document_page_id=page.id,
                    content_type=content_result.content_type,
                    text=content_result.text,
                    bounding_box=content_result.bounding_box,
                    confidence=content_result.confidence,
                    extraction_metadata=content_result.metadata,
                )
            )

        for table_result in page_result.tables:

            table = ExtractedTable(
                document_page_id=page.id,
                table_order=table_result.table_order,
                extraction_method=table_result.extraction_method,
                confidence=table_result.confidence,
                table_metadata=table_result.metadata,
            )

            session.add(table)
            session.flush()

            for cell_result in table_result.cells:

                session.add(
                    ExtractedTableCell(
                        table_id=table.id,
                        row_index=cell_result.row_index,
                        column_index=cell_result.column_index,
                        raw_value=cell_result.raw_value,
                        normalized_value=cell_result.normalized_value,
                        confidence=cell_result.confidence,
                        cell_metadata=cell_result.metadata,
                    )
                )

    session.commit()


def _mark_failed(
    session: Session,
    job: ProcessingJob,
    exc: Exception,
) -> None:

    now = _utcnow()

    current = session.execute(
        select(ProcessingStage)
        .where(
            ProcessingStage.job_id == job.id,
            ProcessingStage.status == "PROCESSING",
        )
        .order_by(ProcessingStage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    error_message = (
        f"{type(exc).__name__}: {str(exc)[:1800]}"
    )

    if current is not None:

        current.status = "FAILED"
        current.error_message = error_message
        current.completed_at = now

    else:

        session.add(
            ProcessingStage(
                job_id=job.id,
                stage="FAILED",
                status="FAILED",
                error_message=error_message,
                completed_at=now,
            )
        )

    job.status = "FAILED"
    job.error_message = error_message
    job.completed_at = now

    session.commit()


def _start_indexing(
    document_id: UUID,
    document_version_id: UUID,
) -> dict:
    """
    Automatically create and execute the search indexing job.

    Render Free does not have a Celery worker, so the existing
    run_indexing_task() is executed directly.
    """

    from sqlalchemy import create_engine

    engine = create_engine(
        SYNC_DATABASE_URL,
        pool_pre_ping=True,
    )

    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    try:

        with session_factory() as session:

            existing = session.execute(
                select(IndexingJob)
                .where(
                    IndexingJob.document_version_id
                    == document_version_id,
                    IndexingJob.status.in_(
                        ["PENDING", "PROCESSING"]
                    ),
                )
                .order_by(
                    IndexingJob.created_at.desc()
                )
                .limit(1)
            ).scalar_one_or_none()

            if existing is not None:

                job_id = existing.id

                logger.info(
                    "Existing indexing job found "
                    "document_version_id=%s job_id=%s",
                    document_version_id,
                    job_id,
                )

            else:

                job = IndexingJob(
                    document_id=document_id,
                    document_version_id=document_version_id,
                    status="PENDING",
                    provider=EMBEDDING_PROVIDER or None,
                    embedding_model=EMBEDDING_MODEL or None,
                    embedding_dimension=VECTOR_EMBEDDING_DIMENSION,
                    requested_by=None,
                )

                session.add(job)
                session.commit()

                job_id = job.id

                logger.info(
                    "Created indexing job "
                    "document_version_id=%s job_id=%s",
                    document_version_id,
                    job_id,
                )

        logger.info(
            "Starting automatic indexing "
            "document_version_id=%s job_id=%s",
            document_version_id,
            job_id,
        )

        result = run_indexing_task(
            str(job_id)
        )

        logger.info(
            "Automatic indexing finished "
            "document_version_id=%s job_id=%s result=%s",
            document_version_id,
            job_id,
            result,
        )

        return result

    finally:

        engine.dispose()


def process_document_job(
    job_id: UUID | str,
    storage: StorageService,
    session_factory: sessionmaker | None = None,
) -> dict:

    from sqlalchemy import create_engine

    engine = None

    if session_factory is None:

        engine = create_engine(
            SYNC_DATABASE_URL,
            pool_pre_ping=True,
        )

        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )

    normalized_job_id = UUID(str(job_id))

    try:

        with session_factory() as session:

            job = session.get(
                ProcessingJob,
                normalized_job_id,
            )

            if job is None:
                raise ProcessingFailure(
                    "Processing job does not exist"
                )

            if job.status == "COMPLETED":

                return {
                    "job_id": str(job.id),
                    "status": "COMPLETED",
                }

            job.started_at = (
                job.started_at or _utcnow()
            )

            job.completed_at = None
            job.error_message = None

            _transition(
                session,
                job,
                "PROCESSING",
            )

            version = session.get(
                DocumentVersion,
                job.document_version_id,
            )

            document = (
                session.get(
                    Document,
                    version.document_id,
                )
                if version
                else None
            )

            if version is None or document is None:

                raise ProcessingFailure(
                    "Document version or document no longer exists"
                )

            processor = ProcessorRouter(
                storage
            ).get_processor(
                document.mime_type,
                document.document_type,
            )

            logger.info(
                "Processor selected job_id=%s processor=%s",
                job.id,
                type(processor).__name__,
            )

            result = processor.process(
                version.storage_key,
                lambda stage: _transition(
                    session,
                    job,
                    stage,
                )
                if job.status != stage
                else None,
            )

            _transition(
                session,
                job,
                "NORMALIZATION",
            )

            _transition(
                session,
                job,
                "VALIDATION",
            )

            _persist_result(
                session,
                job,
                result,
            )

            _transition(
                session,
                job,
                "VERIFICATION_PENDING",
            )

            _transition(
                session,
                job,
                "COMPLETED",
            )

            # ---------------------------------------------------------
            # AUTOMATIC SEARCH INDEXING
            # ---------------------------------------------------------
            #
            # Processing is now complete.
            #
            # Render Free does not have a Celery worker, so we directly
            # execute the existing indexing task here.
            #
            # This makes processed document text available to:
            #   - Query
            #   - Intelligence
            #   - Reports
            #
            # No verification rules are changed here.
            # ---------------------------------------------------------

            index_result = _start_indexing(
                document_id=document.id,
                document_version_id=version.id,
            )

            return {
                "job_id": str(job.id),
                "status": job.status,
                "page_count": len(result.pages),
                "indexing": index_result,
            }

    except Exception as exc:

        logger.error(
            "Processing job failed job_id=%s error_type=%s",
            normalized_job_id,
            type(exc).__name__,
        )

        try:

            with session_factory() as session:

                job = session.get(
                    ProcessingJob,
                    normalized_job_id,
                )

                if (
                    job is not None
                    and job.status != "COMPLETED"
                ):

                    _mark_failed(
                        session,
                        job,
                        exc,
                    )

        except Exception:

            logger.error(
                "Could not mark processing job failed "
                "job_id=%s error_type=%s",
                normalized_job_id,
                type(exc).__name__,
            )

        raise

    finally:

        if engine is not None:
            engine.dispose()
