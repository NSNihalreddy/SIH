import uuid
from datetime import date, datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Mine(Base):
    __tablename__ = "mines"
    __table_args__ = (Index("ix_mines_location", "location", postgresql_using="gist"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(100), unique=True)
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False))
    mine_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    projects: Mapped[list["MiningProject"]] = relationship(back_populates="mine", cascade="all, delete-orphan", passive_deletes=True)
    production_records: Mapped[list["ProductionRecord"]] = relationship(back_populates="mine")


class MiningProject(Base):
    __tablename__ = "mining_projects"
    __table_args__ = (
        CheckConstraint("project_code IS NULL OR length(project_code) > 0", name="project_code_nonempty"),
        Index("ix_mining_projects_mine_name", "mine_id", "name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mines.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_code: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    project_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    mine: Mapped[Mine] = relationship(back_populates="projects")
    boreholes: Mapped[list["Borehole"]] = relationship(back_populates="project")
    production_records: Mapped[list["ProductionRecord"]] = relationship(back_populates="project")


class ProductionRecord(Base):
    __tablename__ = "production_records"
    __table_args__ = (
        CheckConstraint("mine_id IS NOT NULL OR project_id IS NOT NULL", name="mine_or_project_required"),
        CheckConstraint("period_end IS NULL OR period_end >= reporting_period", name="reporting_period_valid"),
        CheckConstraint("extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)", name="confidence_range"),
        Index("ix_production_records_mine_period", "mine_id", "reporting_period"),
        Index("ix_production_records_project_period", "project_id", "reporting_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mines.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mining_projects.id", ondelete="SET NULL"))
    reporting_period: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date)
    production_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    production_unit: Mapped[str | None] = mapped_column(String(50))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    target_unit: Mapped[str | None] = mapped_column(String(50))
    achievement_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    achievement_unit: Mapped[str | None] = mapped_column(String(50))
    achievement_percentage: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    overburden_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    overburden_unit: Mapped[str | None] = mapped_column(String(50))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="SET NULL"), index=True)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    record_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    mine: Mapped[Mine | None] = relationship(back_populates="production_records")
    project: Mapped[MiningProject | None] = relationship(back_populates="production_records")
    source_document: Mapped["Document | None"] = relationship()
    source_page: Mapped["DocumentPage | None"] = relationship()
