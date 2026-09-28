"""Persist external provider batch state for resumable indexing.

Revision ID: 38f2c1a9b47d
Revises: f99c9338e1e2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "38f2c1a9b47d"
down_revision: Union[str, None] = "99633752122f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_indexing_jobs", sa.Column(
        "provider_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("search_indexing_jobs", "provider_metadata")
