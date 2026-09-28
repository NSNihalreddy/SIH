from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import (EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL, EMBEDDING_PROVIDER,
    GEMINI_EMBEDDING_BATCH_SIZE, VECTOR_EMBEDDING_DIMENSION)
from app.db.session import AsyncSessionLocal, engine
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.rag import DocumentChunk, IndexingJob
from app.models.trusted import (CoalBlock, GeologicalFormation, Seam, TrustedMine, TrustedProject,
    TrustedBorehole, TrustedGeologicalMeasurement, TrustedProductionRecord, TrustedCoordinate)
from app.services.chunking import ChunkSpec, chunk_page
from app.services.embeddings import (EmbeddingUnavailable, EmbeddingProviderError,
    _pace_gemini_request, embed_texts)
from app.services import gemini_batch

logger = logging.getLogger(__name__)

TRUSTED_MODELS = [("MINE", TrustedMine), ("PROJECT", TrustedProject), ("BOREHOLE", TrustedBorehole),
    ("COAL_BLOCK", CoalBlock), ("GEOLOGICAL_FORMATION", GeologicalFormation), ("SEAM", Seam),
    ("GEOLOGICAL_MEASUREMENT", TrustedGeologicalMeasurement), ("PRODUCTION", TrustedProductionRecord), ("COORDINATE", TrustedCoordinate)]


def _has_valid_vector(chunk: Any, model: str | None, dimension: int) -> bool:
    vector = getattr(chunk, "embedding", None)
    if (vector is None or getattr(chunk, "embedding_model", None) != model
            or getattr(chunk, "embedding_dimension", None) != dimension):
        return False
    try:
        return len(vector) == dimension and all(math.isfinite(float(value)) for value in vector)
    except (TypeError, ValueError):
        return False


def _needs_embedding(chunk: Any, model: str | None, dimension: int) -> bool:
    return not _has_valid_vector(chunk, model, dimension)


def _refresh_indexed_flag(chunk: Any, source_unchanged: bool, model: str | None, dimension: int) -> bool:
    chunk.is_indexed = bool(source_unchanged and _has_valid_vector(chunk, model, dimension))
    return chunk.is_indexed


def _mark_job_failed(job: IndexingJob, exc: Exception, vector_count: int) -> None:
    job.status = "FAILED"
    job.vector_count = vector_count
    detail = str(exc)[:500] if isinstance(exc, (ValueError, EmbeddingUnavailable, EmbeddingProviderError)) else (
        f"See worker logs for {type(exc).__name__} diagnostics")
    job.error_message = f"Indexing failed ({type(exc).__name__}): {detail}"
    job.completed_at = datetime.now(timezone.utc)


def _valid_index_vector(chunk: DocumentChunk, model: str, dimension: int) -> bool:
    return bool(chunk.is_indexed and _has_valid_vector(chunk, model, dimension))


async def _run_gemini_batch_step(session, job: IndexingJob, version_id: uuid.UUID,
                                 model: str, dimension: int) -> dict:
    """Submit/poll bounded provider batches; commit each completed batch atomically."""
    metadata = dict(job.provider_metadata or {})
    if metadata.get("provider") != "gemini_batch_v1":
        metadata = {"provider": "gemini_batch_v1", "batches": []}
    batches = list(metadata.get("batches") or [])
    outstanding = []
    for batch_index, current in enumerate(batches):
        if current.get("state") in {"FAILED", "CANCELLED", "EXPIRED"}:
            # A later indexing job can retry terminal provider failures while retaining
            # every vector already committed by earlier batches.
            continue
        chunk_ids = [uuid.UUID(value) for value in current["chunk_ids"]]
        polled = await gemini_batch.poll_embedding_batch(current["name"], len(chunk_ids))
        current["state"] = polled["state"]
        if polled["state"] == "RUNNING":
            outstanding.append(current)
            continue
        if polled["state"] != "SUCCEEDED":
            metadata["batches"] = outstanding + [current] + batches[batch_index + 1:]
            job.provider_metadata = metadata
            await session.commit()
            raise EmbeddingProviderError(polled.get("error") or f"Gemini batch ended in {polled['state']}")
        vectors = polled["vectors"]
        if not isinstance(vectors, list) or len(vectors) != len(chunk_ids):
            raise EmbeddingProviderError("Gemini batch result count does not match submitted chunk count")
        for chunk_id, expected_hash, vector in zip(chunk_ids, current["text_sha256"], vectors):
            if len(vector) != dimension or not all(math.isfinite(float(value)) for value in vector):
                raise EmbeddingProviderError(f"Gemini batch vector for chunk {chunk_id} failed dimension or finite-value validation")
            chunk = await session.get(DocumentChunk, chunk_id)
            if chunk is None or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() != expected_hash:
                continue
            chunk.embedding = vector
            chunk.embedding_model, chunk.embedding_dimension = model, dimension
            chunk.is_indexed = True
        await session.flush()
        job.vector_count = await session.scalar(select(func.count(DocumentChunk.id)).where(
            DocumentChunk.document_version_id == version_id, DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_model == model, DocumentChunk.embedding_dimension == dimension,
            DocumentChunk.is_indexed.is_(True))) or 0
        metadata["batches"] = outstanding + batches[batch_index + 1:]
        job.provider_metadata = metadata if metadata["batches"] else None
        await session.commit()
    if outstanding:
        metadata["batches"] = outstanding
        job.provider_metadata = metadata
        await session.commit()
        return {"job_id": str(job.id), "status": "PROCESSING", "vector_count": job.vector_count}
    metadata["batches"] = []
    job.provider_metadata = None

    candidates = list((await session.execute(select(DocumentChunk).where(
        DocumentChunk.document_version_id == version_id,
        or_(DocumentChunk.embedding.is_(None), DocumentChunk.embedding_model.is_distinct_from(model),
            DocumentChunk.embedding_dimension.is_distinct_from(dimension), DocumentChunk.is_indexed.is_(False))
    ).order_by(DocumentChunk.chunk_index).limit(gemini_batch.BATCH_CHUNK_SIZE))).scalars().all())
    pending = []
    for item in candidates:
        if _has_valid_vector(item, model, dimension):
            item.is_indexed = True
        else:
            pending.append(item)
    await session.flush()
    if not pending:
        job.provider_metadata = None
        job.vector_count = await session.scalar(select(func.count(DocumentChunk.id)).where(
            DocumentChunk.document_version_id == version_id, DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_model == model, DocumentChunk.embedding_dimension == dimension,
            DocumentChunk.is_indexed.is_(True))) or 0
        job.status, job.completed_at = "COMPLETED", datetime.now(timezone.utc)
        await session.commit()
        return {"job_id": str(job.id), "status": "COMPLETED", "chunk_count": job.chunk_count, "vector_count": job.vector_count}

    for group in gemini_batch.chunk_groups(pending):
        name = await gemini_batch.create_embedding_batch([item.text for item in group])
        metadata["batches"].append({"name": name, "state": "SUBMITTED",
            "chunk_ids": [str(item.id) for item in group],
            "text_sha256": [hashlib.sha256(item.text.encode("utf-8")).hexdigest() for item in group]})
        job.provider_metadata = metadata
        await session.commit()
    return {"job_id": str(job.id), "status": "PROCESSING", "vector_count": job.vector_count}


def _trusted_text(kind: str, row) -> str:
    if hasattr(row, "canonical_name"):
        values = {"canonical_name": row.canonical_name, "aliases": row.aliases or [], "code": row.domain_code, "attributes": row.attributes or {}}
    elif kind == "GEOLOGICAL_MEASUREMENT":
        values = {"measurement_type": row.measurement_type, "value": str(row.normalized_value) if row.normalized_value is not None else None, "unit": row.unit, "depth_from": str(row.depth_from) if row.depth_from is not None else None, "depth_to": str(row.depth_to) if row.depth_to is not None else None}
    elif kind == "PRODUCTION":
        values = {"reporting_period": row.reporting_period, "commodity": row.commodity, "production_value": str(row.production_value), "unit": row.production_unit}
    else:
        values = {"latitude": str(row.latitude), "longitude": str(row.longitude), "coordinate_system": row.coordinate_system}
    return f"Verified canonical {kind}: " + "; ".join(f"{key}={value}" for key, value in values.items() if value not in (None, "", [], {}))


def _trusted_spec(document_id, version_id, row, kind) -> ChunkSpec:
    text = _trusted_text(kind, row)
    chunk_id = uuid.uuid5(version_id, f"TRUSTED:{kind}:{row.id}:{text}")
    return ChunkSpec(id=chunk_id, document_id=document_id, document_version_id=version_id,
        page_id=row.source_page_id, source_unit_id=row.source_candidate_id, source_unit_type="TRUSTED_RECORD",
        chunk_index=0, text=text, content_type=f"trusted_{kind.lower()}", page_number=None, heading=None,
        metadata={"entity_type": kind, "entity_id": str(row.id), "candidate_id": str(row.source_candidate_id), "verification_id": str(row.verification_id), "verifier_id": str(row.verifier_id)},
        token_count=max(1, len(text)//4), character_count=len(text), evidence_type="TRUSTED_CANONICAL_DATA")


async def run_indexing_job(job_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(IndexingJob).where(IndexingJob.id == job_id).with_for_update())
        if job is None:
            raise LookupError("Indexing job not found")
        if job.status == "COMPLETED":
            return {"job_id": str(job.id), "status": job.status, "chunk_count": job.chunk_count, "vector_count": job.vector_count}
        version_id = job.document_version_id
        document_id = job.document_id
        job_model = job.embedding_model
        job_dimension = job.embedding_dimension
        job.status, job.started_at, job.error_message = "PROCESSING", datetime.now(timezone.utc), None
        await session.commit()

        try:
            if job_model != EMBEDDING_MODEL or job_dimension != VECTOR_EMBEDDING_DIMENSION:
                raise ValueError("Indexing job embedding configuration no longer matches the active provider configuration")
            doc = await session.get(Document, document_id)
            version = await session.get(DocumentVersion, version_id)
            if doc is None or version is None or version.document_id != doc.id:
                raise ValueError("Document version is unavailable")

            # Upsert deterministic chunks in page batches. Existing vector data is
            # retained when text is unchanged and only invalidated when its source changes.
            chunk_total = 0
            offset, page_size = 0, 25
            while True:
                result = await session.execute(select(DocumentPage).options(selectinload(DocumentPage.contents), selectinload(DocumentPage.tables).selectinload(__import__("app.models.intelligence", fromlist=["ExtractedTable"]).ExtractedTable.cells))
                    .where(DocumentPage.document_version_id == version.id).order_by(DocumentPage.page_number.asc().nullslast(), DocumentPage.created_at, DocumentPage.id).offset(offset).limit(page_size))
                pages = list(result.scalars().all())
                if not pages:
                    break
                for page in pages:
                    specs = chunk_page(doc, version, page)
                    existing = (await session.execute(select(DocumentChunk).where(
                        DocumentChunk.document_version_id == version_id,
                        DocumentChunk.document_page_id == page.id))).scalars().all()
                    by_id = {item.id: item for item in existing}
                    for spec in specs:
                        chunk_index = chunk_total
                        chunk_total += 1
                        chunk = by_id.get(spec.id)
                        if chunk is None:
                            chunk = DocumentChunk(id=spec.id, document_id=spec.document_id,
                                document_version_id=spec.document_version_id, document_page_id=spec.page_id,
                                source_unit_id=spec.source_unit_id, source_unit_type=spec.source_unit_type,
                                chunk_index=chunk_index, text=spec.text, token_count=spec.token_count,
                                character_count=spec.character_count,
                                source_metadata={**spec.metadata, "page_number": spec.page_number,
                                    "heading": spec.heading, "evidence_type": spec.evidence_type},
                                content_type=spec.content_type, evidence_type=spec.evidence_type)
                            session.add(chunk)
                        else:
                            text_changed = chunk.text != spec.text
                            if text_changed:
                                chunk.is_indexed = False
                            chunk.document_id, chunk.document_page_id = spec.document_id, spec.page_id
                            chunk.source_unit_id, chunk.source_unit_type = spec.source_unit_id, spec.source_unit_type
                            chunk.chunk_index, chunk.text = chunk_index, spec.text
                            chunk.token_count, chunk.character_count = spec.token_count, spec.character_count
                            chunk.source_metadata = {**spec.metadata, "page_number": spec.page_number,
                                "heading": spec.heading, "evidence_type": spec.evidence_type}
                            chunk.content_type, chunk.evidence_type = spec.content_type, spec.evidence_type
                            _refresh_indexed_flag(chunk, not text_changed, job_model, job_dimension)
                job.chunk_count = chunk_total
                await session.commit()
                session.expunge_all()
                offset += page_size
            for kind, model in TRUSTED_MODELS:
                trusted_offset = 0
                while True:
                    trusted_result = await session.execute(select(model).where(model.source_version_id == version.id).order_by(model.id).offset(trusted_offset).limit(100))
                    trusted_rows = list(trusted_result.scalars().all())
                    if not trusted_rows:
                        break
                    for row in trusted_rows:
                        spec = _trusted_spec(doc.id, version.id, row, kind)
                        chunk = await session.get(DocumentChunk, spec.id)
                        if chunk is None:
                            chunk = DocumentChunk(id=spec.id, document_id=spec.document_id,
                                document_version_id=spec.document_version_id, document_page_id=spec.page_id,
                                source_unit_id=spec.source_unit_id, source_unit_type=spec.source_unit_type,
                                chunk_index=chunk_total, text=spec.text, token_count=spec.token_count,
                                character_count=spec.character_count,
                                source_metadata={**spec.metadata, "page_number": spec.page_number,
                                    "evidence_type": spec.evidence_type}, content_type=spec.content_type,
                                evidence_type=spec.evidence_type)
                            session.add(chunk)
                        else:
                            text_changed = chunk.text != spec.text
                            if text_changed:
                                chunk.is_indexed = False
                            chunk.document_id, chunk.document_page_id = spec.document_id, spec.page_id
                            chunk.source_unit_id, chunk.source_unit_type = spec.source_unit_id, spec.source_unit_type
                            chunk.chunk_index, chunk.text = chunk_total, spec.text
                            chunk.token_count, chunk.character_count = spec.token_count, spec.character_count
                            chunk.source_metadata = {**spec.metadata, "page_number": spec.page_number,
                                "evidence_type": spec.evidence_type}
                            chunk.content_type, chunk.evidence_type = spec.content_type, spec.evidence_type
                            _refresh_indexed_flag(chunk, not text_changed, job_model, job_dimension)
                        chunk_total += 1
                    job.chunk_count = chunk_total
                    await session.commit()
                    session.expunge_all()
                    trusted_offset += 100
            job = await session.get(IndexingJob, job_id)
            job.chunk_count = chunk_total
            valid_vector_filter = (DocumentChunk.document_version_id == version_id,
                DocumentChunk.embedding.is_not(None), DocumentChunk.embedding_model == job_model,
                DocumentChunk.embedding_dimension == job_dimension, DocumentChunk.is_indexed.is_(True))
            stored_vectors = await session.scalar(select(func.count(DocumentChunk.id)).where(*valid_vector_filter)) or 0
            job.vector_count = stored_vectors
            await session.commit()
            if not chunk_total:
                raise ValueError("No processed page content or trusted data is available to index")

            if EMBEDDING_PROVIDER == "gemini":
                return await _run_gemini_batch_step(session, job, version_id, job_model, job_dimension)

            batch_size = EMBEDDING_BATCH_SIZE
            previous_batch_succeeded = False
            while True:
                batch = list((await session.execute(select(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
                    .where(or_(DocumentChunk.embedding.is_(None),
                        DocumentChunk.embedding_model.is_distinct_from(job_model),
                        DocumentChunk.embedding_dimension.is_distinct_from(job_dimension),
                        DocumentChunk.is_indexed.is_(False)))
                    .order_by(DocumentChunk.chunk_index).limit(batch_size))).scalars().all())
                if not batch:
                    break
                await _pace_gemini_request(previous_batch_succeeded)
                vectors = await embed_texts([item.text for item in batch])
                if len(vectors) != len(batch):
                    raise ValueError("Embedding batch returned an unexpected number of vectors")
                for item, vector in zip(batch, vectors):
                    if len(vector) != job_dimension or not all(math.isfinite(float(value)) for value in vector):
                        raise ValueError("Embedding dimension changed during indexing")
                    item.embedding, item.embedding_model, item.embedding_dimension = vector, job_model, job_dimension
                    item.is_indexed = True
                job.vector_count = stored_vectors + len(batch)
                await session.commit()
                stored_vectors += len(batch)
                previous_batch_succeeded = True
            job.status, job.completed_at = "COMPLETED", datetime.now(timezone.utc)
            await session.commit()
            logger.info("Indexing completed job_id=%s chunks=%d vectors=%d", job.id, job.chunk_count, job.vector_count)
            return {"job_id": str(job.id), "status": job.status, "chunk_count": job.chunk_count, "vector_count": job.vector_count}
        except Exception as exc:
            await session.rollback()
            # Keep every previously committed valid vector and derive progress from storage.
            job = await session.get(IndexingJob, job_id)
            valid_vectors = await session.scalar(select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_version_id == version_id, DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == job_model, DocumentChunk.embedding_dimension == job_dimension,
                DocumentChunk.is_indexed.is_(True))) or 0
            _mark_job_failed(job, exc, valid_vectors)
            await session.commit()
            logger.error("Indexing failed job_id=%s error_type=%s", job_id, type(exc).__name__)
            return {"job_id": str(job.id), "status": job.status, "error": job.error_message}


def run_indexing_task(job_id: str) -> dict:
    async def run_and_dispose() -> dict:
        try:
            return await run_indexing_job(uuid.UUID(job_id))
        finally:
            # Celery invokes this sync task through asyncio.run; close asyncpg
            # connections before that event loop exits.
            await engine.dispose()

    return asyncio.run(run_and_dispose())
