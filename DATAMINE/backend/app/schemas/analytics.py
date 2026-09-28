import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnalyticsFilter(BaseModel):
    entity_type: Literal["MINE", "PROJECT"] | None = None
    entity_id: uuid.UUID | None = None
    year: int | None = Field(default=None, ge=1800, le=2200)
    year_from: int | None = Field(default=None, ge=1800, le=2200)
    year_to: int | None = Field(default=None, ge=1800, le=2200)
    state: str | None = Field(default=None, max_length=100)
    commodity: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_period_scope(self):
        if self.entity_id is not None and self.entity_type is None:
            raise ValueError("entity_type is required when entity_id is supplied")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from must not exceed year_to")
        return self


class CalculationRequest(BaseModel):
    calculation_type: Literal[
        "production_difference", "growth_percentage", "production_share", "sum",
        "average", "minimum", "maximum", "median", "standard_deviation", "cagr",
        "stripping_ratio", "productivity",
    ]
    source_record_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    entity_type: Literal["MINE", "PROJECT", "PORTFOLIO"] = "PORTFOLIO"
    entity_id: uuid.UUID | None = None
    years: int | None = Field(default=None, ge=1, le=1000)
