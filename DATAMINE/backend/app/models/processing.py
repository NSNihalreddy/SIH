import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

PROCESSING_STAGES = (
    "QUEUED", "PROCESSING", "PARSING", "OCR", "TABLE_EXTRACTION",
    "NORMALIZATION", "VALIDATION", "VERIFICATION_PENDING", "COMPLETED", "FAILED",
)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED','PROCESSING','PARSING','OCR','TABLE_EXTRACTION','NORMALIZATION','VALIDATION','VERIFICATION_PENDING','COMPLETED','FAILED')", name="status_valid"),
        Index("ix_processing_jobs_version_status", "document_version_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="QUEUED", index=True)
    processor: Mapped[str | None] = mapped_column(String(150))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    document_version: Mapped["DocumentVersion"] = relationship()
    stages: Mapped[list["ProcessingStage"]] = relationship(back_populates="job", cascade="all, delete-orphan", passive_deletes=True)


class ProcessingStage(Base):
    __tablename__ = "processing_stages"
    __table_args__ = (
        CheckConstraint("stage IN ('QUEUED','PROCESSING','PARSING','OCR','TABLE_EXTRACTION','NORMALIZATION','VALIDATION','VERIFICATION_PENDING','COMPLETED','FAILED')", name="stage_valid"),
        Index("ix_processing_stages_job_created", "job_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="QUEUED")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job: Mapped[ProcessingJob] = relationship(back_populates="stages")
