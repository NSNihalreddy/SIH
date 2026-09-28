import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ExtractionCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_page_id: uuid.UUID | None
    source_content_id: uuid.UUID | None
    source_table_id: uuid.UUID | None
    source_cell_id: uuid.UUID | None
    candidate_type: str
    raw_text: str
    raw_value: str | None
    normalized_value: str | None
    normalized_numeric_value: Decimal | None
    unit: str | None
    extraction_confidence: Decimal | None
    verification_status: str
    match_status: str
    validation_results: list
    candidate_metadata: dict
    created_at: datetime


class ExtractionRunResponse(BaseModel):
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    candidate_count: int
    candidate_counts: dict[str, int]
    unresolved_count: int
    validation_failure_count: int
    duration_seconds: float
