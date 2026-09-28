from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import CHUNK_OVERLAP_CHARACTERS, MAX_CHUNK_CHARACTERS


@dataclass(frozen=True)
class ChunkSpec:
    id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    page_id: uuid.UUID | None
    source_unit_id: uuid.UUID
    source_unit_type: str
    chunk_index: int
    text: str
    content_type: str
    page_number: int | None
    heading: str | None
    metadata: dict[str, Any]
    token_count: int
    character_count: int
    evidence_type: str = "SOURCE_DOCUMENT"


def _split(text: str, max_chars: int = MAX_CHUNK_CHARACTERS, overlap: int = CHUNK_OVERLAP_CHARACTERS) -> list[str]:
    clean = "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()).strip()
    if not clean:
        return []
    parts: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + max_chars, len(clean))
        if end < len(clean):
            boundary = max(clean.rfind("\n", start, end), clean.rfind(". ", start, end), clean.rfind(" ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + (1 if clean[boundary:boundary + 1] == "." else 0)
        part = clean[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return parts


def _specs_for_unit(*, doc_id, version_id, page, unit_id, unit_type, content_type, text, heading=None, metadata=None, index_start=0):
    output = []
    for ordinal, part in enumerate(_split(text)):
        digest = hashlib.sha256(part.encode("utf-8")).hexdigest()
        chunk_id = uuid.uuid5(version_id, f"{unit_type}:{unit_id}:{ordinal}:{digest}")
        output.append(ChunkSpec(id=chunk_id, document_id=doc_id, document_version_id=version_id,
            page_id=page.id if page else None, source_unit_id=unit_id, source_unit_type=unit_type,
            chunk_index=index_start + ordinal, text=part, content_type=content_type,
            page_number=page.page_number if page else None, heading=heading,
            metadata={**(metadata or {}), "heading": heading, "source_unit_type": unit_type},
            token_count=max(1, len(part) // 4), character_count=len(part)))
    return output


def chunk_page(document, version, page) -> list[ChunkSpec]:
    """Chunk a single page and retain its source-unit identity and table row context."""
    specs: list[ChunkSpec] = []
    page_meta = page.page_metadata or {}
    heading = page_meta.get("heading") or page_meta.get("section")
    def content_order(item):
        meta = item.extraction_metadata or {}
        box = item.bounding_box or {}
        return (meta.get("block_index", 10**9), meta.get("line_index", 10**9), meta.get("span_index", 10**9),
            box.get("y0", 0), box.get("x0", 0), item.created_at or 0, str(item.id))
    contents = sorted(page.contents or [], key=content_order)
    if contents:
        span_groups: dict[int, list] = {}
        remaining_contents = []
        for content in contents:
            if content.content_type == "text_span":
                block_index = int((content.extraction_metadata or {}).get("block_index", 0))
                span_groups.setdefault(block_index, []).append(content)
            else:
                remaining_contents.append(content)
        for block_index, spans in sorted(span_groups.items()):
            lines: dict[int, list] = {}
            for span in spans:
                line_index = int((span.extraction_metadata or {}).get("line_index", 0))
                lines.setdefault(line_index, []).append(span)
            block_text = "\n".join("".join(span.text for span in sorted(line, key=lambda item: (int((item.extraction_metadata or {}).get("span_index", 0)), str(item.id)))) for _, line in sorted(lines.items()))
            source_ids = [str(span.id) for span in spans]
            specs.extend(_specs_for_unit(doc_id=document.id, version_id=version.id, page=page, unit_id=spans[0].id,
                unit_type="TEXT_BLOCK", content_type="text_span", text=block_text, heading=heading,
                metadata={"block_index": block_index, "source_unit_ids": source_ids, "bounding_boxes": [span.bounding_box for span in spans if span.bounding_box]}, index_start=len(specs)))
        for content in remaining_contents:
            content_heading = (content.extraction_metadata or {}).get("heading") or heading
            specs.extend(_specs_for_unit(doc_id=document.id, version_id=version.id, page=page, unit_id=content.id,
                unit_type="CONTENT", content_type=content.content_type, text=content.text, heading=content_heading,
                metadata={"confidence": float(content.confidence) if content.confidence is not None else None,
                    "bounding_box": content.bounding_box, "extraction_metadata": content.extraction_metadata or {}}, index_start=len(specs)))
    elif page.text:
        specs.extend(_specs_for_unit(doc_id=document.id, version_id=version.id, page=page, unit_id=page.id,
            unit_type="PAGE", content_type="page_text", text=page.text, heading=heading,
            metadata={"page_metadata": page_meta}, index_start=len(specs)))

    for table in sorted(page.tables or [], key=lambda item: (item.table_order, str(item.id))):
        cells = sorted(table.cells or [], key=lambda cell: (cell.row_index, cell.column_index))
        rows: dict[int, list] = {}
        for cell in cells:
            rows.setdefault(cell.row_index, []).append(cell)
        header_cells = rows.get(min(rows), []) if rows else []
        headers = {cell.column_index: (cell.normalized_value or cell.raw_value or f"Column {cell.column_index + 1}").strip() for cell in header_cells}
        start_row = min(rows) + 1 if len(rows) > 1 else min(rows) if rows else 0
        for row_index in sorted(row for row in rows if row >= start_row):
            row_cells = rows[row_index]
            row_text = " | ".join(f"{headers.get(cell.column_index, f'Column {cell.column_index + 1}')}: {(cell.normalized_value or cell.raw_value or '').strip()}" for cell in row_cells)
            if not row_text.strip(" |:"):
                continue
            specs.extend(_specs_for_unit(doc_id=document.id, version_id=version.id, page=page, unit_id=table.id,
                unit_type="TABLE_ROW", content_type="table_row", text=f"Table {table.table_order}, row {row_index + 1}. Headers: {' | '.join(headers.values())}. {row_text}",
                heading=heading, metadata={"table_id": str(table.id), "table_order": table.table_order, "row_index": row_index,
                    "cell_ids": [str(cell.id) for cell in row_cells], "headers": headers}, index_start=len(specs)))
    return specs
