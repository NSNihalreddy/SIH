import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExtractionCandidate(Base):
    """Deterministic, provenance-bearing extraction output awaiting review."""

    __tablename__ = "extraction_candidates"
    __table_args__ = (
        Index("ix_extraction_candidates_version_type", "document_version_id", "candidate_type"),
        Index("ix_extraction_candidates_document_page", "document_id", "source_page_id"),
        Index("ix_extraction_candidates_verification", "verification_status"),
        Index("ix_extraction_candidates_classification", "classification"),
        Index("ix_extraction_candidates_classification_status", "classification_status"),
        Index("ix_extraction_candidates_mapping_status", "mapping_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="SET NULL"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="SET NULL"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="SET NULL"))
    candidate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    normalized_numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    unit: Mapped[str | None] = mapped_column(String(80))
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    classification: Mapped[str] = mapped_column(String(50), nullable=False, default="UNKNOWN")
    classification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_CLASSIFIED")
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    classification_rule: Mapped[str | None] = mapped_column(String(150))
    match_status: Mapped[str] = mapped_column(String(30), nullable=False, default="UNRESOLVED")
    mapping_status: Mapped[str] = mapped_column(String(30), nullable=False, default="UNRESOLVED")
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifier_reason: Mapped[str | None] = mapped_column(Text)
    validation_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    candidate_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
