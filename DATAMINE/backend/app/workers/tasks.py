from app.storage.factory import create_storage_service
from app.workers.celery_app import celery_app
from app.workers.processing import process_document_job
from app.services.search_index import run_indexing_task
from app.services.gemini_batch import POLL_SECONDS


def _claim_intelligence_run(session, run_id: str):
    """Lock and transition exactly one delivery from PENDING/QUEUED to PROCESSING."""
    from datetime import datetime, timezone
    import uuid
    from sqlalchemy import select
    from app.models.analytics import IntelligenceRun

    run_uuid = uuid.UUID(str(run_id))
    run = session.execute(
        select(IntelligenceRun)
        .where(IntelligenceRun.id == run_uuid)
        .with_for_update()
    ).scalar_one_or_none()

    if run is None or run.status in {"COMPLETED", "PROCESSING"}:
        return None

    if run.status not in {"PENDING", "QUEUED"}:
        return None

    run.status = "PROCESSING"
    run.started_at = run.started_at or datetime.now(timezone.utc)
    session.commit()

    return run


@celery_app.task(
    name="app.workers.tasks.process_document_task",
    bind=True,
    acks_late=True,
)
def process_document_task(self, job_id: str) -> dict:
    return process_document_job(
        job_id,
        create_storage_service(),
    )


@celery_app.task(
    name="app.workers.tasks.index_document_version_task",
    bind=True,
    acks_late=True,
)
def index_document_version_task(self, job_id: str) -> dict:
    """
    Run document indexing without requiring a separate Celery worker.

    Render Free does not provide a Celery worker for this deployment.
    Gemini batch indexing can return PROCESSING while the provider is
    still processing the submitted batch. Therefore, keep polling here
    instead of sending the next poll back to Celery.
    """
    import time

    while True:
        result = run_indexing_task(job_id)

        status = result.get("status")

        if status != "PROCESSING":
            return result

        time.sleep(POLL_SECONDS)


@celery_app.task(
    name="app.workers.tasks.build_intelligence_task",
    bind=True,
    acks_late=True,
)
def build_intelligence_task(self, run_id: str) -> dict:
    from datetime import datetime, timezone
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.core.config import (
        SYNC_DATABASE_URL,
        EMBEDDING_MODEL,
        VECTOR_EMBEDDING_DIMENSION,
    )
    from app.models.analytics import IntelligenceRun
    from app.models.rag import DocumentChunk
    from app.models.documents import DocumentPage
    from app.services.intelligence import extract_document_intelligence

    engine = create_engine(
        SYNC_DATABASE_URL,
        pool_pre_ping=True,
    )

    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    try:
        with factory() as session:
            run = _claim_intelligence_run(
                session,
                run_id,
            )

            if run is None:
                return {
                    "run_id": run_id,
                    "status": "NOT_CLAIMED",
                }

            stmt = (
                select(
                    DocumentChunk,
                    DocumentPage.page_number,
                )
                .outerjoin(
                    DocumentPage,
                    DocumentPage.id == DocumentChunk.document_page_id,
                )
                .where(
                    DocumentChunk.is_indexed.is_(True),
                    DocumentChunk.embedding.is_not(None),
                    DocumentChunk.embedding_model == EMBEDDING_MODEL,
                    DocumentChunk.embedding_dimension
                    == VECTOR_EMBEDDING_DIMENSION,
                )
            )

            if run.document_id:
                stmt = stmt.where(
                    DocumentChunk.document_id == run.document_id
                )

            if run.document_version_id:
                stmt = stmt.where(
                    DocumentChunk.document_version_id
                    == run.document_version_id
                )

            rows = session.execute(
                stmt.order_by(
                    DocumentChunk.document_id,
                    DocumentChunk.chunk_index,
                )
            ).all()

            chunks = [
                {
                    "chunk_id": c.id,
                    "document_id": c.document_id,
                    "document_version_id": c.document_version_id,
                    "page_id": c.document_page_id,
                    "page_number": (
                        page_number
                        or (c.source_metadata or {}).get("page_number")
                    ),
                    "source_unit_id": c.source_unit_id,
                    "text": c.text,
                    "evidence_type": c.evidence_type,
                }
                for c, page_number in rows
            ]

            result = extract_document_intelligence(chunks)

            run.source_chunk_count = len(chunks)
            result["generated_at"] = datetime.now(timezone.utc).isoformat()
            run.result = result
            run.status = "COMPLETED"
            run.completed_at = datetime.now(timezone.utc)

            session.commit()

            return {
                "run_id": str(run.id),
                "status": run.status,
                "chunk_count": len(chunks),
            }

    except Exception as exc:
        try:
            with factory() as session:
                run = session.get(
                    IntelligenceRun,
                    run_id,
                )

                if run:
                    run.status = "FAILED"
                    run.error_message = (
                        f"{type(exc).__name__}: {str(exc)[:1000]}"
                    )
                    run.completed_at = datetime.now(timezone.utc)
                    session.commit()

        except Exception:
            pass

        raise

    finally:
        engine.dispose()


@celery_app.task(
    name="app.workers.tasks.generate_report_task",
    bind=True,
    acks_late=True,
)
def generate_report_task(self, report_id: str) -> dict:
    from app.services.report_generation import generate_report

    return generate_report(report_id)
