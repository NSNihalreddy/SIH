"""Align geography expression index SQL with mapped cast expressions.

Revision ID: b11c7e2a4d91
Revises: b11c7e2a4d90
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision: str = "b11c7e2a4d91"
down_revision: Union[str, None] = "b11c7e2a4d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    for table, index in (
        ("trusted_coordinates", "ix_trusted_coordinates_geometry_geog_gist"),
        ("trusted_boreholes", "ix_trusted_boreholes_location_geog_gist"),
        ("trusted_mines", "ix_trusted_mines_location_geog_gist"),
    ):
        op.drop_index(index, table_name=table)
    for table, column, index in (
        ("trusted_mines", "location", "ix_trusted_mines_location_geog_gist"),
        ("trusted_boreholes", "location", "ix_trusted_boreholes_location_geog_gist"),
        ("trusted_coordinates", "geometry", "ix_trusted_coordinates_geometry_geog_gist"),
    ):
        op.create_index(index, table, [sa.cast(sa.column(column), Geography())], postgresql_using="gist")


def downgrade() -> None:
    for table, index in (
        ("trusted_coordinates", "ix_trusted_coordinates_geometry_geog_gist"),
        ("trusted_boreholes", "ix_trusted_boreholes_location_geog_gist"),
        ("trusted_mines", "ix_trusted_mines_location_geog_gist"),
    ):
        op.drop_index(index, table_name=table)
    for table, column, index in (
        ("trusted_mines", "location", "ix_trusted_mines_location_geog_gist"),
        ("trusted_boreholes", "location", "ix_trusted_boreholes_location_geog_gist"),
        ("trusted_coordinates", "geometry", "ix_trusted_coordinates_geometry_geog_gist"),
    ):
        op.create_index(index, table, [sa.text(f"({column}::geography)")], postgresql_using="gist")
