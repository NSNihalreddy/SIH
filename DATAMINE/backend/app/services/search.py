from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import DateTime, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import EMBEDDING_MODEL, LEXICAL_WEIGHT, SEMANTIC_WEIGHT, VECTOR_EMBEDDING_DIMENSION
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.rag import DocumentChunk
from app.services.embeddings import embed_query


async def search_chunks(session: AsyncSession, query: str, *, top_k: int = 10, filters: dict[str, Any] | None = None, mode: str = "hybrid") -> list[dict]:
    if len(query) > 1000:
        raise ValueError("Query exceeds the 1000 character limit")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    filters = filters or {}
    if mode not in {"semantic", "hybrid", "lexical"}:
        raise ValueError("Unsupported retrieval mode")
    query_vector = None
    if mode != "lexical":
        query_vector = await embed_query(query)
    sem_score = (1.0 - DocumentChunk.embedding.cosine_distance(query_vector)) if query_vector is not None else literal(0.0)
    tsv = func.to_tsvector("simple", DocumentChunk.text)
    tsq = func.plainto_tsquery("simple", query)
    raw_lexical = func.ts_rank_cd(tsv, tsq)
    lexical_score = raw_lexical / (1.0 + raw_lexical)
    if mode == "semantic":
        score = sem_score
    elif mode == "lexical":
        score = lexical_score
    else:
        divisor = SEMANTIC_WEIGHT + LEXICAL_WEIGHT
        score = sem_score * (SEMANTIC_WEIGHT / divisor) + lexical_score * (LEXICAL_WEIGHT / divisor)

    stmt = select(DocumentChunk, Document, DocumentVersion, DocumentPage,
        sem_score.label("semantic_score"), lexical_score.label("lexical_score"), score.label("score")) \
        .join(Document, Document.id == DocumentChunk.document_id) \
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id) \
        .outerjoin(DocumentPage, DocumentPage.id == DocumentChunk.document_page_id)
    # Lexical retrieval works from stored source chunks before embedding. Vector
    # and hybrid retrieval require an actually persisted, validated vector.
    if mode != "lexical":
        stmt = stmt.where(DocumentChunk.is_indexed.is_(True))
    if query_vector is not None:
        stmt = stmt.where(DocumentChunk.embedding.is_not(None), DocumentChunk.embedding_model == EMBEDDING_MODEL,
            DocumentChunk.embedding_dimension == VECTOR_EMBEDDING_DIMENSION)
    if filters.get("document_id"):
        stmt = stmt.where(Document.id == filters["document_id"])
    if filters.get("document_version_id"):
        stmt = stmt.where(DocumentVersion.id == filters["document_version_id"])
    if filters.get("page_id"):
        stmt = stmt.where(DocumentPage.id == filters["page_id"])
    if filters.get("content_type"):
        stmt = stmt.where(DocumentChunk.content_type == filters["content_type"])
    evidence_types = filters.get("evidence_types")
    if evidence_types:
        stmt = stmt.where(DocumentChunk.evidence_type.in_(evidence_types))
    entity_type = filters.get("entity_type")
    if entity_type:
        stmt = stmt.where(DocumentChunk.source_metadata["entity_type"].astext == entity_type.upper())
    if filters.get("date_from"):
        stmt = stmt.where(Document.created_at >= filters["date_from"])
    if filters.get("date_to"):
        stmt = stmt.where(Document.created_at < filters["date_to"])

    # Stable tie breaking by UUID; source joins guarantee a traceable document/version.
    rows = (await session.execute(stmt.order_by(score.desc(), DocumentChunk.id.asc()).limit(top_k))).all()
    output = []
    for chunk, document, version, page, semantic, lexical, total in rows:
        if not chunk.document_id or not chunk.document_version_id or not chunk.source_unit_id:
            continue
        output.append({"result_id": str(chunk.id), "score": float(total), "semantic_score": float(semantic),
            "lexical_score": float(lexical), "text": chunk.text, "document_id": str(document.id),
            "document_version_id": str(version.id), "page_id": str(page.id) if page else None,
            "page_number": page.page_number if page else (chunk.source_metadata or {}).get("page_number"),
            "source_unit_id": str(chunk.source_unit_id), "source_unit_type": chunk.source_unit_type,
            "document_name": document.original_filename, "content_type": chunk.content_type,
            "evidence_type": chunk.evidence_type, "provenance": {"document_checksum": document.sha256_checksum,
                "version_checksum": version.sha256_checksum, "chunk_id": str(chunk.id),
                "source_metadata": chunk.source_metadata or {}}, "chunk": chunk})
    return output
