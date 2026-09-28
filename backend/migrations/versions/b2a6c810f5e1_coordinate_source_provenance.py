"""Add complete source provenance to trusted coordinates.

Revision ID: b2a6c810f5e1
Revises: e8b72c1f6a90
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2a6c810f5e1"
down_revision: Union[str, None] = "e8b72c1f6a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    op.add_column("trusted_coordinates", sa.Column("source_content_id", sa.Uuid(), nullable=True))
    op.add_column("trusted_coordinates", sa.Column("source_table_id", sa.Uuid(), nullable=True))
    op.add_column("trusted_coordinates", sa.Column("source_cell_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_trusted_coordinates_source_content_id_extracted_content",
        "trusted_coordinates", "extracted_content", ["source_content_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trusted_coordinates_source_table_id_extracted_tables",
        "trusted_coordinates", "extracted_tables", ["source_table_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_trusted_coordinates_source_cell_id_extracted_table_cells",
        "trusted_coordinates", "extracted_table_cells", ["source_cell_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_trusted_coordinates_source_cell_id_extracted_table_cells", "trusted_coordinates", type_="foreignkey")
    op.drop_constraint("fk_trusted_coordinates_source_table_id_extracted_tables", "trusted_coordinates", type_="foreignkey")
    op.drop_constraint("fk_trusted_coordinates_source_content_id_extracted_content", "trusted_coordinates", type_="foreignkey")
    op.drop_column("trusted_coordinates", "source_cell_id")
    op.drop_column("trusted_coordinates", "source_table_id")
    op.drop_column("trusted_coordinates", "source_content_id")
