from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProcessingStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    stage: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime


class ProcessingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_version_id: UUID
    status: str
    processor: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ProcessingStatusResponse(BaseModel):
    job: ProcessingJobResponse
    stages: list[ProcessingStageResponse]


class ExtractedContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    content_type: str
    text: str
    bounding_box: dict | None
    confidence: Decimal | None
    extraction_metadata: dict | None


class ExtractedTableCellResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    row_index: int
    column_index: int
    raw_value: str | None
    normalized_value: str | None
    confidence: Decimal | None
    cell_metadata: dict | None


class ExtractedTableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    table_order: int
    extraction_method: str | None
    confidence: Decimal | None
    table_metadata: dict | None
    cells: list[ExtractedTableCellResponse]


class DocumentPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_version_id: UUID
    page_number: int | None
    text: str | None
    page_metadata: dict | None
    created_at: datetime
    contents: list[ExtractedContentResponse]
    tables: list[ExtractedTableResponse]
