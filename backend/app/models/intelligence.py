import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ExtractedContent(Base):
    __tablename__ = "extracted_content"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        Index("ix_extracted_content_page_type", "document_page_id", "content_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bounding_box: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    extraction_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document_page: Mapped["DocumentPage"] = relationship(back_populates="contents")


class ExtractedTable(Base):
    __tablename__ = "extracted_tables"
    __table_args__ = (
        UniqueConstraint("document_page_id", "table_order"),
        CheckConstraint("table_order > 0", name="table_order_positive"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        Index("ix_extracted_tables_document_page", "document_page_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False)
    table_order: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_method: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    table_metadata: Mapped[dict | None] = mapped_column(JSONB)

    document_page: Mapped["DocumentPage"] = relationship(back_populates="tables")
    cells: Mapped[list["ExtractedTableCell"]] = relationship(back_populates="table", cascade="all, delete-orphan", passive_deletes=True)


class ExtractedTableCell(Base):
    __tablename__ = "extracted_table_cells"
    __table_args__ = (
        UniqueConstraint("table_id", "row_index", "column_index"),
        CheckConstraint("row_index >= 0 AND column_index >= 0", name="cell_indexes_nonnegative"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        Index("ix_extracted_table_cells_table", "table_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extracted_tables.id", ondelete="CASCADE"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    column_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    cell_metadata: Mapped[dict | None] = mapped_column(JSONB)

    table: Mapped[ExtractedTable] = relationship(back_populates="cells")
