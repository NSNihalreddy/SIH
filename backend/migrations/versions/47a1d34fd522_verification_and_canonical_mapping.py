"""Add canonical mapping, verification, and conflict records.

Revision ID: 47a1d34fd522
Revises: a63e821bd094
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "47a1d34fd522"
down_revision = "a63e821bd094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_candidates", sa.Column("classification", sa.String(50), server_default="UNKNOWN", nullable=False))
    op.add_column("extraction_candidates", sa.Column("classification_confidence", sa.Numeric(5, 4), nullable=True))
    op.add_column("extraction_candidates", sa.Column("classification_rule", sa.String(150), nullable=True))
    op.add_column("extraction_candidates", sa.Column("mapping_status", sa.String(30), server_default="UNRESOLVED", nullable=False))
    op.add_column("extraction_candidates", sa.Column("claimed_by", sa.Uuid(), nullable=True))
    op.add_column("extraction_candidates", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("extraction_candidates", sa.Column("verified_by", sa.Uuid(), nullable=True))
    op.add_column("extraction_candidates", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("extraction_candidates", sa.Column("verifier_reason", sa.Text(), nullable=True))
    op.create_foreign_key("fk_extraction_candidates_claimed_by_users", "extraction_candidates", "users", ["claimed_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_extraction_candidates_verified_by_users", "extraction_candidates", "users", ["verified_by"], ["id"], ondelete="SET NULL")
    op.create_index("ix_extraction_candidates_classification", "extraction_candidates", ["classification"])
    op.create_index("ix_extraction_candidates_mapping_status", "extraction_candidates", ["mapping_status"])

    op.create_table(
        "canonical_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_key", sa.String(300), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_entities")),
        sa.UniqueConstraint("entity_type", "normalized_key", name="uq_canonical_entity_type_key"),
    )
    op.create_table(
        "mapping_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("classification", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("rule", sa.String(150), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("proposed_entity_id", sa.Uuid(), nullable=True),
        sa.Column("possible_entity_id", sa.Uuid(), nullable=True),
        sa.Column("proposal_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["extraction_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["possible_entity_id"], ["canonical_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposed_entity_id"], ["canonical_entities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_proposals")),
        sa.UniqueConstraint("candidate_id", name=op.f("uq_mapping_proposals_candidate_id")),
    )
    op.create_index("ix_mapping_proposals_status_created", "mapping_proposals", ["status", "created_at"])
    op.create_table(
        "canonical_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_entity_id", sa.Uuid(), nullable=True),
        sa.Column("record_type", sa.String(60), nullable=False),
        sa.Column("original_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("accepted_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=True),
        sa.Column("source_content_id", sa.Uuid(), nullable=True),
        sa.Column("source_table_id", sa.Uuid(), nullable=True),
        sa.Column("source_cell_id", sa.Uuid(), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["extraction_candidates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["canonical_entity_id"], ["canonical_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_version_id"], ["document_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_page_id"], ["document_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_content_id"], ["extracted_content.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_table_id"], ["extracted_tables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_cell_id"], ["extracted_table_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_records")),
        sa.UniqueConstraint("candidate_id", name=op.f("uq_canonical_records_candidate_id")),
    )
    op.create_index("ix_canonical_records_entity_created", "canonical_records", ["canonical_entity_id", "created_at"])
    op.create_index(op.f("ix_canonical_records_canonical_entity_id"), "canonical_records", ["canonical_entity_id"])
    op.create_table(
        "verification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous_state", sa.String(30), nullable=False),
        sa.Column("new_state", sa.String(30), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["extraction_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_events")),
    )
    op.create_index("ix_verification_events_candidate_created", "verification_events", ["candidate_id", "created_at"])
    op.create_table(
        "extraction_conflicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conflict_type", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_conflicts")),
    )
    op.create_index("ix_extraction_conflicts_status_created", "extraction_conflicts", ["status", "created_at"])
    op.create_table(
        "extraction_conflict_candidates",
        sa.Column("conflict_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["conflict_id"], ["extraction_conflicts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["extraction_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conflict_id", "candidate_id", name=op.f("pk_extraction_conflict_candidates")),
    )


def downgrade() -> None:
    op.drop_table("extraction_conflict_candidates")
    op.drop_index("ix_extraction_conflicts_status_created", table_name="extraction_conflicts")
    op.drop_table("extraction_conflicts")
    op.drop_index("ix_verification_events_candidate_created", table_name="verification_events")
    op.drop_table("verification_events")
    op.drop_index(op.f("ix_canonical_records_canonical_entity_id"), table_name="canonical_records")
    op.drop_index("ix_canonical_records_entity_created", table_name="canonical_records")
    op.drop_table("canonical_records")
    op.drop_index("ix_mapping_proposals_status_created", table_name="mapping_proposals")
    op.drop_table("mapping_proposals")
    op.drop_table("canonical_entities")
    op.drop_index("ix_extraction_candidates_mapping_status", table_name="extraction_candidates")
    op.drop_index("ix_extraction_candidates_classification", table_name="extraction_candidates")
    op.drop_constraint("fk_extraction_candidates_verified_by_users", "extraction_candidates", type_="foreignkey")
    op.drop_constraint("fk_extraction_candidates_claimed_by_users", "extraction_candidates", type_="foreignkey")
    for name in ("verifier_reason", "verified_at", "verified_by", "claimed_at", "claimed_by", "mapping_status", "classification_rule", "classification_confidence", "classification"):
        op.drop_column("extraction_candidates", name)
