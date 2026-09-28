"""Persisted Step 12 intelligence runs.

Revision ID: c42d18a7e601
Revises: b11c7e2a4d91
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c42d18a7e601"
down_revision: Union[str, None] = "b11c7e2a4d91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intelligence_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_chunk_count", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_intelligence_runs_status", "intelligence_runs", ["status"])
    op.create_index("ix_intelligence_runs_document_id", "intelligence_runs", ["document_id"])
    op.create_index("ix_intelligence_runs_document_version_id", "intelligence_runs", ["document_version_id"])
    op.create_index("ix_intelligence_runs_status_created", "intelligence_runs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_intelligence_runs_status_created", table_name="intelligence_runs")
    op.drop_index("ix_intelligence_runs_document_version_id", table_name="intelligence_runs")
    op.drop_index("ix_intelligence_runs_document_id", table_name="intelligence_runs")
    op.drop_index("ix_intelligence_runs_status", table_name="intelligence_runs")
    op.drop_table("intelligence_runs")
