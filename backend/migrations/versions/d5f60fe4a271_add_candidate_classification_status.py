"""Distinguish unprocessed candidates from classified unknowns.

Revision ID: d5f60fe4a271
Revises: 47a1d34fd522
"""
from alembic import op
import sqlalchemy as sa

revision = "d5f60fe4a271"
down_revision = "47a1d34fd522"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_candidates", sa.Column("classification_status", sa.String(30), server_default="NOT_CLASSIFIED", nullable=False))
    op.create_index("ix_extraction_candidates_classification_status", "extraction_candidates", ["classification_status"])
    op.execute(sa.text("UPDATE extraction_candidates SET classification_status = CASE WHEN classification_rule IS NULL THEN 'NOT_CLASSIFIED' WHEN classification = 'UNKNOWN' THEN 'UNKNOWN' ELSE 'CLASSIFIED' END"))


def downgrade() -> None:
    op.drop_index("ix_extraction_candidates_classification_status", table_name="extraction_candidates")
    op.drop_column("extraction_candidates", "classification_status")
