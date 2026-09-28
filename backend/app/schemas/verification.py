import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ClaimRequest(BaseModel):
    pass


class ReviewRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
    canonical_entity_id: uuid.UUID | None = None


class EditApproveRequest(BaseModel):
    edited_value: dict[str, Any]
    reason: str = Field(min_length=1, max_length=2000)
    canonical_entity_id: uuid.UUID | None = None


class ConflictResolutionRequest(BaseModel):
    status: Literal["RESOLVED", "ACCEPTED_AS_SOURCE_VARIATION"]
    resolution: str = Field(min_length=1, max_length=4000)


class ClassificationBatchRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    document_id: uuid.UUID | None = None
