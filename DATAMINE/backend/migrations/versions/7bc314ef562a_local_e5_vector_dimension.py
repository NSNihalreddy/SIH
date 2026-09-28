"""Switch semantic vector storage to native multilingual-e5-base dimensions.

Revision ID: 7bc314ef562a
Revises: 38f2c1a9b47d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "7bc314ef562a"
down_revision: Union[str, None] = "38f2c1a9b47d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty_vector_column() -> None:
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM document_chunks WHERE embedding IS NOT NULL) THEN
                RAISE EXCEPTION 'Clear semantic vectors through the version-scoped maintenance endpoint before changing vector dimension';
            END IF;
        END $$;
    """))


def upgrade() -> None:
    _require_empty_vector_column()
    op.drop_index("ix_document_chunks_embedding_cosine", table_name="document_chunks")
    op.alter_column("document_chunks", "embedding", type_=Vector(768),
        existing_nullable=True, postgresql_using="embedding::text::vector(768)")
    op.create_index("ix_document_chunks_embedding_cosine", "document_chunks", ["embedding"],
        postgresql_using="ivfflat", postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": 100}, postgresql_where=sa.text("embedding IS NOT NULL"))


def downgrade() -> None:
    _require_empty_vector_column()
    op.drop_index("ix_document_chunks_embedding_cosine", table_name="document_chunks")
    op.alter_column("document_chunks", "embedding", type_=Vector(1536),
        existing_nullable=True, postgresql_using="embedding::text::vector(1536)")
    op.create_index("ix_document_chunks_embedding_cosine", "document_chunks", ["embedding"],
        postgresql_using="ivfflat", postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": 100}, postgresql_where=sa.text("embedding IS NOT NULL"))
