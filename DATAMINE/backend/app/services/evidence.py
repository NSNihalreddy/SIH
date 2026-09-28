from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MAX_CONTEXT_CHARACTERS
from app.models.documents import DocumentPage
from app.models.intelligence import ExtractedContent, ExtractedTableCell
from app.models.rag import DocumentChunk


async def retrieve_evidence(session: AsyncSession, result: dict) -> dict:
    chunk: DocumentChunk = result["chunk"]
    page = await session.get(DocumentPage, chunk.document_page_id) if chunk.document_page_id else None
    metadata = chunk.source_metadata or {}
    context_parts: list[str] = []
    if page and page.text:
        context_parts.append(page.text[:MAX_CONTEXT_CHARACTERS])
    if page:
        units = (await session.execute(select(ExtractedContent).where(ExtractedContent.document_page_id == page.id).order_by(ExtractedContent.created_at, ExtractedContent.id).limit(10))).scalars().all()
        for unit in units:
            if unit.text and unit.id != chunk.source_unit_id:
                context_parts.append(unit.text[:4000])
    row_indexes = metadata.get("row_index")
    table_id = metadata.get("table_id")
    if table_id and row_indexes is not None:
        cells = (await session.execute(select(ExtractedTableCell).where(ExtractedTableCell.table_id == uuid.UUID(str(table_id)),
            ExtractedTableCell.row_index == row_indexes).order_by(ExtractedTableCell.column_index))).scalars().all()
        if cells:
            context_parts.append("Table row context: " + " | ".join(cell.normalized_value or cell.raw_value or "" for cell in cells))
    context = "\n".join(dict.fromkeys(part for part in context_parts if part))
    return {key: result[key] for key in ("result_id", "score", "semantic_score", "lexical_score", "text", "document_id",
        "document_version_id", "page_id", "page_number", "source_unit_id", "source_unit_type", "document_name",
        "content_type", "evidence_type", "provenance")} | {"context": context[:MAX_CONTEXT_CHARACTERS], "exact_source_text": chunk.text}
