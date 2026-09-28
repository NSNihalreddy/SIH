import time
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_roles
from app.core.config import (EMBEDDING_MODEL, EMBEDDING_PROVIDER, LLM_MODEL,
    VECTOR_EMBEDDING_DIMENSION, SEMANTIC_WEIGHT, LEXICAL_WEIGHT)
from app.db.session import get_db
from app.models.documents import Document, DocumentVersion
from app.models.identity import User
from app.models.rag import DocumentChunk, IndexingJob, SearchAudit
from app.services.embeddings import EmbeddingProviderError, EmbeddingUnavailable, embedding_configuration
from app.services.llm import llm_configuration
from app.services.rag import answer_question
from app.services.search import search_chunks
from app.workers.tasks import index_document_version_task

router = APIRouter(prefix="/api/v1", tags=["semantic search and RAG"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Indexer = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]
Admin = Annotated[User, Depends(require_roles("ADMIN"))]


class SearchFilters(BaseModel):
    document_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    page_id: uuid.UUID | None = None
    content_type: str | None = Field(default=None, max_length=80)
    date_from: datetime | None = None
    date_to: datetime | None = None
    entity_type: str | None = Field(default=None, max_length=60)
    evidence_types: list[str] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=50)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class RAGRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=20)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class ClearVectorsRequest(BaseModel):
    confirm_version_id: uuid.UUID
    expected_vector_count: int = Field(ge=1)
    expected_embedding_model: str = Field(min_length=1, max_length=200)
    expected_embedding_dimension: int = Field(ge=1)


async def _audit(session, actor, query, count, ids, provider, model, status, start):
    session.add(SearchAudit(actor_id=actor.id, query_text=query, retrieval_count=count,
        selected_evidence_ids=ids, provider=provider, model=model, response_status=status,
        latency_ms=round((time.perf_counter()-start)*1000)))
    await session.commit()


@router.post("/search/semantic")
async def semantic_search(request: SearchRequest, actor: Reader, session: AsyncSession = Depends(get_db)):
    start = time.perf_counter()
    try:
        results = await search_chunks(session, request.query.strip(), top_k=request.top_k,
            filters=request.filters.model_dump(exclude_none=True), mode="hybrid")
    except EmbeddingUnavailable as exc:
        await _audit(session, actor, request.query, 0, [], EMBEDDING_PROVIDER or None, EMBEDDING_MODEL or None, "EMBEDDING_UNAVAILABLE", start)
        raise HTTPException(503, {"status": "EMBEDDING_UNAVAILABLE", "detail": str(exc)}) from exc
    except EmbeddingProviderError as exc:
        await _audit(session, actor, request.query, 0, [], EMBEDDING_PROVIDER or None, EMBEDDING_MODEL or None, "EMBEDDING_FAILED", start)
        raise HTTPException(503, {"status": "EMBEDDING_FAILED", "detail": str(exc)}) from exc
    from app.services.evidence import retrieve_evidence
    evidence = [await retrieve_evidence(session, item) for item in results]
    await _audit(session, actor, request.query, len(evidence), [item["result_id"] for item in evidence], EMBEDDING_PROVIDER or None, EMBEDDING_MODEL or None, "COMPLETED", start)
    return {"items": evidence, "total": len(evidence), "query": request.query,
        "retrieval_metadata": {"mode": "hybrid", "semantic_weight": SEMANTIC_WEIGHT,
            "lexical_weight": LEXICAL_WEIGHT,
            "embedding": embedding_configuration()}}


@router.post("/search/lexical")
async def lexical_search(request: SearchRequest, actor: Reader, session: AsyncSession = Depends(get_db)):
    start = time.perf_counter()
    results = await search_chunks(session, request.query.strip(), top_k=request.top_k,
        filters=request.filters.model_dump(exclude_none=True), mode="lexical")
    from app.services.evidence import retrieve_evidence
    evidence = [await retrieve_evidence(session, item) for item in results]
    await _audit(session, actor, request.query, len(evidence), [item["result_id"] for item in evidence], "postgresql_fts", None, "COMPLETED", start)
    return {"items": evidence, "total": len(evidence), "query": request.query, "retrieval_metadata": {"mode": "lexical"}}


@router.post("/rag/query")
async def rag_query(
    request: RAGRequest,
    actor: Reader,
    session: AsyncSession = Depends(get_db),
):
    start = time.perf_counter()
    filters = request.filters.model_dump(exclude_none=True)

    try:
        # Primary path: hybrid semantic + lexical RAG
        result = await answer_question(
            session,
            request.question,
            request.top_k,
            filters,
        )

        model = llm_configuration()

        await _audit(
            session,
            actor,
            request.question,
            len(result["evidence"]),
            [item["result_id"] for item in result["evidence"]],
            model["provider"],
            model["model"],
            result["status"],
            start,
        )

        return result

   except (EmbeddingUnavailable, EmbeddingProviderError, OSError) as exc:
        # Render fallback:
        # semantic embeddings are unavailable, so use PostgreSQL
        # lexical retrieval instead of returning HTTP 503.
        from app.services.evidence import retrieve_evidence

        try:
            results = await search_chunks(
                session,
                request.question.strip(),
                top_k=request.top_k,
                filters=filters,
                mode="lexical",
            )

            evidence = [
                await retrieve_evidence(session, item)
                for item in results
            ]

            evidence_ids = [
                item["result_id"]
                for item in evidence
            ]

            await _audit(
                session,
                actor,
                request.question,
                len(evidence),
                evidence_ids,
                "postgresql_fts",
                None,
                "LEXICAL_FALLBACK",
                start,
            )

            if not evidence:
                return {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "answer": (
                        "No matching source evidence was found "
                        "for this question."
                    ),
                    "evidence": [],
                    "limitations": [
                        "Semantic embeddings are unavailable.",
                        "Lexical retrieval returned no matching evidence.",
                    ],
                    "retrieval_metadata": {
                        "mode": "lexical_fallback",
                        "embedding_error": str(exc),
                    },
                }

            # Evidence-bound response without relying on the broken
            # local embedding model.
            answer_parts = []

            for index, item in enumerate(evidence[:request.top_k], 1):
                text_value = (
                    item.get("text")
                    or item.get("content")
                    or ""
                ).strip()

                if text_value:
                    answer_parts.append(
                        f"[E{index}] {text_value[:1200]}"
                    )

            answer = (
                "The following source evidence was retrieved "
                "for your question:\n\n"
                + "\n\n".join(answer_parts)
            )

            return {
                "status": "LEXICAL_FALLBACK",
                "answer": answer,
                "evidence": evidence,
                "limitations": [
                    "Semantic retrieval was unavailable.",
                    "Results were retrieved using lexical "
                    "source-text matching.",
                ],
                "retrieval_metadata": {
                    "mode": "lexical_fallback",
                    "semantic_weight": 0,
                    "lexical_weight": 1,
                    "embedding_error": str(exc),
                },
            }

        except Exception as fallback_exc:
            import traceback
            traceback.print_exc()
            
            await _audit(
                session,
                actor,
                request.question,
                0,
                [],
                "postgresql_fts",
                None,
                "LEXICAL_FALLBACK_FAILED",
                start,
            )

            raise HTTPException(
                503,
                {
                    "status": "QUERY_UNAVAILABLE",
                    "detail": (
                        "Semantic retrieval is unavailable and "
                        "lexical fallback also failed."
                    ),
                },
            ) from fallback_exc


async def _submit_index(session: AsyncSession, document: Document, version: DocumentVersion, actor: User, force: bool = False):
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:version_id, 11))"), {"version_id": str(version.id)})
    latest = await session.scalar(select(IndexingJob).where(IndexingJob.document_version_id == version.id).order_by(IndexingJob.created_at.desc()).limit(1).with_for_update())
    if latest and latest.status in {"PENDING", "PROCESSING"}:
        raise HTTPException(409, {"detail": "Indexing is already active", "job_id": str(latest.id), "status": latest.status})
    if latest and latest.status == "COMPLETED" and not force and latest.embedding_model == EMBEDDING_MODEL and latest.provider == (EMBEDDING_PROVIDER or None) and latest.embedding_dimension == VECTOR_EMBEDDING_DIMENSION:
        return latest, False
    job = IndexingJob(document_id=document.id, document_version_id=version.id, status="PENDING",
        provider=EMBEDDING_PROVIDER or None, embedding_model=EMBEDDING_MODEL or None,
        embedding_dimension=VECTOR_EMBEDDING_DIMENSION, requested_by=actor.id,
        provider_metadata=(latest.provider_metadata if latest and latest.status == "FAILED"
            and latest.provider == (EMBEDDING_PROVIDER or None)
            and latest.embedding_model == EMBEDDING_MODEL
            and latest.embedding_dimension == VECTOR_EMBEDDING_DIMENSION else None))
    session.add(job)
    await session.commit()
    try:
        index_document_version_task.apply_async(args=[str(job.id)])
    except Exception as exc:
        job = await session.get(IndexingJob, job.id)
        job.status, job.completed_at = "FAILED", datetime.now(timezone.utc)
        job.error_message = f"Could not submit indexing task ({type(exc).__name__})"
        await session.commit()
        raise HTTPException(503, {"status": "FAILED", "job_id": str(job.id), "detail": "Indexing queue is unavailable"}) from exc
    return job, True


async def _latest_version(session: AsyncSession, document_id: uuid.UUID):
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    version = await session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.version_number.desc()).limit(1))
    if version is None:
        raise HTTPException(409, "Document has no version")
    return document, version


@router.post("/search/index/documents/{document_id}", status_code=202)
async def index_document(document_id: uuid.UUID, actor: Indexer, session: AsyncSession = Depends(get_db)):
    doc, version = await _latest_version(session, document_id)
    job, submitted = await _submit_index(session, doc, version, actor)
    return {"job_id": job.id, "status": job.status, "submitted": submitted}


@router.post("/search/index/versions/{version_id}", status_code=202)
async def index_version(version_id: uuid.UUID, actor: Indexer, session: AsyncSession = Depends(get_db)):
    version = await session.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(404, "Document version not found")
    doc = await session.get(Document, version.document_id)
    job, submitted = await _submit_index(session, doc, version, actor)
    return {"job_id": job.id, "status": job.status, "submitted": submitted}


@router.post("/search/reindex/{document_id}", status_code=202)
async def reindex_document(document_id: uuid.UUID, actor: Indexer, session: AsyncSession = Depends(get_db)):
    doc, version = await _latest_version(session, document_id)
    job, submitted = await _submit_index(session, doc, version, actor, force=True)
    return {"job_id": job.id, "status": job.status, "submitted": submitted}


@router.get("/search/index/status/{job_id}")
async def indexing_status(job_id: uuid.UUID, actor: Reader, session: AsyncSession = Depends(get_db)):
    job = await session.get(IndexingJob, job_id)
    if job is None:
        raise HTTPException(404, "Indexing job not found")
    provider_batch = None
    metadata = job.provider_metadata or {}
    if metadata.get("provider") == "gemini_batch_v1":
        provider_batch = [{"name": item.get("name"), "state": item.get("state"),
            "chunk_count": len(item.get("chunk_ids") or [])} for item in metadata.get("batches", [])]
    return {"job_id": job.id, "document_id": job.document_id, "document_version_id": job.document_version_id,
        "status": job.status, "provider": job.provider, "embedding_model": job.embedding_model,
        "embedding_dimension": job.embedding_dimension, "chunk_count": job.chunk_count,
        "vector_count": job.vector_count, "error_message": job.error_message,
        "started_at": job.started_at, "completed_at": job.completed_at, "created_at": job.created_at,
        "provider_batches": provider_batch}


@router.post("/search/index/versions/{version_id}/clear-vectors")
async def clear_version_vectors(version_id: uuid.UUID, request: ClearVectorsRequest,
                                actor: Admin, session: AsyncSession = Depends(get_db)):
    """Clear only explicitly confirmed embedding fields for one version before a dimension migration."""
    if request.confirm_version_id != version_id:
        raise HTTPException(400, "Confirmation version id must match the route version id")
    version = await session.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(404, "Document version not found")
    active = await session.scalar(select(IndexingJob.id).where(
        IndexingJob.document_version_id == version_id,
        IndexingJob.status.in_(["PENDING", "PROCESSING"])).limit(1))
    if active:
        raise HTTPException(409, "Cannot clear vectors while indexing is active")
    base_filter = (DocumentChunk.document_version_id == version_id, DocumentChunk.embedding.is_not(None))
    total = await session.scalar(select(func.count(DocumentChunk.id)).where(*base_filter)) or 0
    matching = await session.scalar(select(func.count(DocumentChunk.id)).where(
        *base_filter, DocumentChunk.embedding_model == request.expected_embedding_model,
        DocumentChunk.embedding_dimension == request.expected_embedding_dimension)) or 0
    if total != request.expected_vector_count or matching != total:
        raise HTTPException(409, {"detail": "Stored vector count or model/dimension does not match confirmation",
            "actual_vector_count": total, "matching_vector_count": matching})
    await session.execute(update(DocumentChunk).where(*base_filter).values(
        embedding=None, embedding_model=None, embedding_dimension=None, is_indexed=False))
    session.add(SearchAudit(actor_id=actor.id,
        query_text=f"Clear semantic vectors for document version {version_id}",
        retrieval_count=0, selected_evidence_ids=[], provider=EMBEDDING_PROVIDER,
        model=EMBEDDING_MODEL, response_status="VECTOR_REBUILD_PREPARED", latency_ms=0))
    await session.commit()
    return {"document_version_id": str(version_id), "cleared_vector_count": total,
        "chunks_preserved": True, "source_and_provenance_preserved": True}
