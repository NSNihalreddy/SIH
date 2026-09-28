from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    document_type: str | None
    mime_type: str | None
    file_size: int
    sha256_checksum: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int
    sha256_checksum: str | None
    file_size: int
    created_at: datetime
    created_by: UUID | None
