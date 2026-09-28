"""evidence-backed reports and generated artifact metadata"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e8b72c1f6a90"
down_revision: Union[str, None] = "c42d18a7e601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_version_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_references", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_reports_idempotency_key")),
    )
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_index("ix_reports_requested_by", "reports", ["requested_by"])
    op.create_index("ix_reports_status_created", "reports", ["status", "created_at"])
    op.create_table(
        "report_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(12), nullable=False),
        sa.Column("storage_key", sa.String(600), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name=op.f("uq_report_artifacts_storage_key")),
    )
    op.create_index("ix_report_artifacts_report", "report_artifacts", ["report_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_report_artifacts_report", table_name="report_artifacts")
    op.drop_table("report_artifacts")
    op.drop_index("ix_reports_status_created", table_name="reports")
    op.drop_index("ix_reports_requested_by", table_name="reports")
    op.drop_index("ix_reports_status", table_name="reports")
    op.drop_table("reports")
