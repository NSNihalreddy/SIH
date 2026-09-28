"""Add provenance-bearing structured extraction candidates.

Revision ID: a63e821bd094
Revises: f99c9338e1e2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a63e821bd094"
down_revision = "f99c9338e1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=True),
        sa.Column("source_content_id", sa.Uuid(), nullable=True),
        sa.Column("source_table_id", sa.Uuid(), nullable=True),
        sa.Column("source_cell_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_type", sa.String(length=80), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("normalized_numeric_value", sa.Numeric(24, 9), nullable=True),
        sa.Column("unit", sa.String(length=80), nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False),
        sa.Column("match_status", sa.String(length=30), nullable=False),
        sa.Column("validation_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("candidate_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_page_id"], ["document_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_content_id"], ["extracted_content.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_table_id"], ["extracted_tables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_cell_id"], ["extracted_table_cells.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_candidates")),
    )
    op.create_index("ix_extraction_candidates_version_type", "extraction_candidates", ["document_version_id", "candidate_type"])
    op.create_index("ix_extraction_candidates_document_page", "extraction_candidates", ["document_id", "source_page_id"])
    op.create_index("ix_extraction_candidates_verification", "extraction_candidates", ["verification_status"])
    op.create_index(op.f("ix_extraction_candidates_source_page_id"), "extraction_candidates", ["source_page_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_extraction_candidates_source_page_id"), table_name="extraction_candidates")
    op.drop_index("ix_extraction_candidates_verification", table_name="extraction_candidates")
    op.drop_index("ix_extraction_candidates_document_page", table_name="extraction_candidates")
    op.drop_index("ix_extraction_candidates_version_type", table_name="extraction_candidates")
    op.drop_table("extraction_candidates")
