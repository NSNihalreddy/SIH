import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CanonicalEntity(Base):
    __tablename__ = "canonical_entities"
    __table_args__ = (UniqueConstraint("entity_type", "normalized_key", name="uq_canonical_entity_type_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(300), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MappingProposal(Base):
    __tablename__ = "mapping_proposals"
    __table_args__ = (Index("ix_mapping_proposals_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="CASCADE"), nullable=False, unique=True)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    rule: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    proposed_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id", ondelete="SET NULL"))
    possible_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id", ondelete="SET NULL"))
    proposal_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalRecord(Base):
    __tablename__ = "canonical_records"
    __table_args__ = (Index("ix_canonical_records_entity_created", "canonical_entity_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False, unique=True)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id", ondelete="SET NULL"), index=True)
    record_type: Mapped[str] = mapped_column(String(60), nullable=False)
    original_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    accepted_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"))
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="SET NULL"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="SET NULL"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="SET NULL"))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class VerificationEvent(Base):
    __tablename__ = "verification_events"
    __table_args__ = (Index("ix_verification_events_candidate_created", "candidate_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_state: Mapped[str] = mapped_column(String(30), nullable=False)
    new_state: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExtractionConflict(Base):
    __tablename__ = "extraction_conflicts"
    __table_args__ = (Index("ix_extraction_conflicts_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conflict_type: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="OPEN")
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExtractionConflictCandidate(Base):
    __tablename__ = "extraction_conflict_candidates"

    conflict_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_conflicts.id", ondelete="CASCADE"), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="CASCADE"), primary_key=True)
