import uuid
from datetime import datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Borehole(Base):
    __tablename__ = "boreholes"
    __table_args__ = (
        CheckConstraint("total_depth IS NULL OR total_depth >= 0", name="total_depth_nonnegative"),
        Index("ix_boreholes_location", "location", postgresql_using="gist"),
        Index("ix_boreholes_project_identifier", "project_id", "borehole_identifier", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    borehole_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mining_projects.id", ondelete="SET NULL"), index=True)
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False))
    elevation: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    total_depth: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    borehole_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source_document: Mapped["Document | None"] = relationship()
    project: Mapped["MiningProject | None"] = relationship()
    intervals: Mapped[list["BoreholeInterval"]] = relationship(back_populates="borehole", cascade="all, delete-orphan", passive_deletes=True)
    measurements: Mapped[list["GeologicalMeasurement"]] = relationship(back_populates="borehole", cascade="all, delete-orphan", passive_deletes=True)


class BoreholeInterval(Base):
    __tablename__ = "borehole_intervals"
    __table_args__ = (
        CheckConstraint("top_depth >= 0 AND bottom_depth >= top_depth", name="depth_range_valid"),
        CheckConstraint("extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)", name="confidence_range"),
        Index("ix_borehole_intervals_borehole_depth", "borehole_id", "top_depth", "bottom_depth"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    borehole_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boreholes.id", ondelete="CASCADE"), nullable=False)
    top_depth: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    bottom_depth: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    lithology: Mapped[str | None] = mapped_column(String(150))
    seam_material: Mapped[str | None] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    interval_metadata: Mapped[dict | None] = mapped_column(JSONB)

    borehole: Mapped[Borehole] = relationship(back_populates="intervals")
    source_document: Mapped["Document | None"] = relationship(foreign_keys=[source_document_id])
    source_page: Mapped["DocumentPage | None"] = relationship()


class GeologicalMeasurement(Base):
    __tablename__ = "geological_measurements"
    __table_args__ = (
        CheckConstraint("value IS NULL OR value::text NOT IN ('NaN','Infinity','-Infinity')", name="value_finite"),
        CheckConstraint("depth IS NULL OR depth >= 0", name="depth_nonnegative"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        Index("ix_geological_measurements_borehole_type", "borehole_id", "measurement_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    borehole_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("boreholes.id", ondelete="SET NULL"), index=True)
    measurement_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(50))
    depth: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    measurement_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    borehole: Mapped[Borehole | None] = relationship(back_populates="measurements")
    source_document: Mapped["Document | None"] = relationship(foreign_keys=[source_document_id])
    source_page: Mapped["DocumentPage | None"] = relationship()
