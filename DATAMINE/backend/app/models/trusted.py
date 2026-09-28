import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TrustedDomainMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    domain_code: Mapped[str | None] = mapped_column(String(100), index=True)
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("canonical_entities.id", ondelete="RESTRICT"), unique=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="RESTRICT"))
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="RESTRICT"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="RESTRICT"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="RESTRICT"))
    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_records.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class CoalBlock(TrustedDomainMixin, Base):
    __tablename__ = "trusted_coal_blocks"
    mineral: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[dict | None] = mapped_column(JSONB)


class TrustedMine(TrustedDomainMixin, Base):
    __tablename__ = "trusted_mines"
    __table_args__ = (Index("ix_trusted_mines_location_gist", "location", postgresql_using="gist"),)
    operator: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str | None] = mapped_column(String(100))
    district: Mapped[str | None] = mapped_column(String(100))
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False))
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)


class TrustedProject(TrustedDomainMixin, Base):
    __tablename__ = "trusted_projects"
    mine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_mines.id", ondelete="RESTRICT"))
    project_type: Mapped[str | None] = mapped_column(String(100))
    organization: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[dict | None] = mapped_column(JSONB)


class TrustedBorehole(TrustedDomainMixin, Base):
    __tablename__ = "trusted_boreholes"
    __table_args__ = (Index("ix_trusted_boreholes_location_gist", "location", postgresql_using="gist"),)
    mine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_mines.id", ondelete="RESTRICT"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_projects.id", ondelete="RESTRICT"))
    location = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False))
    elevation: Mapped[float | None] = mapped_column(Numeric(12, 3))
    depth: Mapped[float | None] = mapped_column(Numeric(12, 3))
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)


class GeologicalFormation(TrustedDomainMixin, Base):
    __tablename__ = "trusted_geological_formations"
    description: Mapped[str | None] = mapped_column(Text)


class Seam(TrustedDomainMixin, Base):
    __tablename__ = "trusted_seams"
    formation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_geological_formations.id", ondelete="RESTRICT"))
    commodity: Mapped[str | None] = mapped_column(String(100))


class TrustedGeologicalMeasurement(Base):
    __tablename__ = "trusted_geological_measurements"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    measurement_type: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[float | None] = mapped_column(Numeric(20, 8))
    unit: Mapped[str | None] = mapped_column(String(50))
    depth_from: Mapped[float | None] = mapped_column(Numeric(12, 3))
    depth_to: Mapped[float | None] = mapped_column(Numeric(12, 3))
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="RESTRICT"))
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="RESTRICT"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="RESTRICT"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="RESTRICT"))
    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_records.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    measurement_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="VERIFIED")
    borehole_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_boreholes.id", ondelete="RESTRICT"))
    seam_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_seams.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TrustedProductionRecord(Base):
    __tablename__ = "trusted_production_records"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_mines.id", ondelete="RESTRICT"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_projects.id", ondelete="RESTRICT"))
    reporting_period: Mapped[str] = mapped_column(String(40), nullable=False)
    commodity: Mapped[str | None] = mapped_column(String(100))
    production_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    production_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="VERIFIED")
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="RESTRICT"))
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="RESTRICT"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="RESTRICT"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="RESTRICT"))
    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_records.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TrustedCoordinate(Base):
    __tablename__ = "trusted_coordinates"
    __table_args__ = (Index("ix_trusted_coordinates_geometry_gist", "geometry", postgresql_using="gist"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    latitude: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    coordinate_system: Mapped[str] = mapped_column(String(50), nullable=False)
    geometry = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False)
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_pages.id", ondelete="RESTRICT"))
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_content.id", ondelete="RESTRICT"))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_tables.id", ondelete="RESTRICT"))
    source_cell_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extracted_table_cells.id", ondelete="RESTRICT"))
    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_records.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalValueHistory(Base):
    __tablename__ = "canonical_value_history"
    __table_args__ = (Index("ix_canonical_value_history_entity_field", "entity_type", "entity_id", "field_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    accepted_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_candidates.id", ondelete="RESTRICT"), nullable=False)
    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_records.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalDataConflict(Base):
    __tablename__ = "canonical_data_conflicts"
    __table_args__ = (Index("ix_canonical_data_conflicts_status_created", "status", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value_a: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance_a: Mapped[dict] = mapped_column(JSONB, nullable=False)
    value_b: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance_b: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _geography_index(name: str, table, expression: str) -> None:
    index = Index(name, text(expression), postgresql_using="gist")
    index._set_parent(table)


_geography_index("ix_trusted_mines_location_geog_gist", TrustedMine.__table__, "(location::geography(Geometry,4326))")
_geography_index("ix_trusted_boreholes_location_geog_gist", TrustedBorehole.__table__, "(location::geography(Geometry,4326))")
_geography_index("ix_trusted_coordinates_geometry_geog_gist", TrustedCoordinate.__table__, "(geometry::geography(Geometry,4326))")
