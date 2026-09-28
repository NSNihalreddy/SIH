"""Add deterministic analytics calculation and validation result storage.

Revision ID: a10d3f4c9b20
Revises: 7bc314ef562a, f99c9338e1e2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a10d3f4c9b20"
down_revision: Union[str, Sequence[str], None] = ("7bc314ef562a", "f99c9338e1e2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_calculation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("calculation_type", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_value", sa.Numeric(30, 12)),
        sa.Column("result_unit", sa.String(80)),
        sa.Column("formula_id", sa.String(100), nullable=False),
        sa.Column("formula_version", sa.String(30), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("verification_status", sa.String(30), nullable=False),
        sa.Column("validation_status", sa.String(30), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_analytics_calc_type_created", "analytics_calculation_results", ["calculation_type", "calculated_at"])
    op.create_index("ix_analytics_calc_entity", "analytics_calculation_results", ["entity_type", "entity_id"])
    op.create_table(
        "analytics_validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("validation_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("compared_records", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("difference", sa.Numeric(30, 12)),
        sa.Column("tolerance", sa.Numeric(30, 12)),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analytics_validation_created", "analytics_validation_results", ["created_at"])
    op.create_index("ix_analytics_validation_type", "analytics_validation_results", ["validation_type", "status"])


def downgrade() -> None:
    op.drop_index("ix_analytics_validation_type", table_name="analytics_validation_results")
    op.drop_index("ix_analytics_validation_created", table_name="analytics_validation_results")
    op.drop_table("analytics_validation_results")
    op.drop_index("ix_analytics_calc_entity", table_name="analytics_calculation_results")
    op.drop_index("ix_analytics_calc_type_created", table_name="analytics_calculation_results")
    op.drop_table("analytics_calculation_results")
