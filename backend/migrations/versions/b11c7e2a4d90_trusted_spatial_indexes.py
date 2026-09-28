"""Index existing trusted PostGIS point geometries for GIS searches.

Revision ID: b11c7e2a4d90
Revises: a10d3f4c9b20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision: str = "b11c7e2a4d90"
down_revision: Union[str, None] = "a10d3f4c9b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_trusted_mines_location_gist", "trusted_mines", ["location"], postgresql_using="gist")
    op.create_index("ix_trusted_boreholes_location_gist", "trusted_boreholes", ["location"], postgresql_using="gist")
    op.create_index("ix_trusted_coordinates_geometry_gist", "trusted_coordinates", ["geometry"], postgresql_using="gist")
    # Radius and metre-distance operations cast points to geography; these expression
    # indexes keep ST_DWithin queries indexable without changing any stored geometry.
    op.create_index("ix_trusted_mines_location_geog_gist", "trusted_mines",
        [sa.cast(sa.column("location"), Geography())], postgresql_using="gist")
    op.create_index("ix_trusted_boreholes_location_geog_gist", "trusted_boreholes",
        [sa.cast(sa.column("location"), Geography())], postgresql_using="gist")
    op.create_index("ix_trusted_coordinates_geometry_geog_gist", "trusted_coordinates",
        [sa.cast(sa.column("geometry"), Geography())], postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_trusted_coordinates_geometry_geog_gist", table_name="trusted_coordinates")
    op.drop_index("ix_trusted_boreholes_location_geog_gist", table_name="trusted_boreholes")
    op.drop_index("ix_trusted_mines_location_geog_gist", table_name="trusted_mines")
    op.drop_index("ix_trusted_coordinates_geometry_gist", table_name="trusted_coordinates")
    op.drop_index("ix_trusted_boreholes_location_gist", table_name="trusted_boreholes")
    op.drop_index("ix_trusted_mines_location_gist", table_name="trusted_mines")
