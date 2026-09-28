import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.auth import get_current_user
from app.db.session import get_db
from app.gis.coordinates import normalize_coordinates, point_geometry, validate_bbox
from app.gis.queries import count_spatial_entities, spatial_entities
from app.gis.relationships import boreholes_near_mines
from app.gis.validation import validate_spatial_record
from app.main import app


def test_coordinate_normalization_is_repeatable_wgs84_and_creates_point():
    expected = {"latitude": "12.5", "longitude": "77.25", "srid": 4326,
        "geometry_wkt": "SRID=4326;POINT(77.25 12.5)"}
    assert normalize_coordinates("12.500", "77.2500") == expected
    assert normalize_coordinates("12.500", "77.2500") == expected
    assert point_geometry(12.5, 77.25).srid == 4326


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181), (float("nan"), 0), (0, float("inf"))])
def test_invalid_geographic_coordinates_rejected(lat, lon):
    with pytest.raises(ValueError): normalize_coordinates(lat, lon)


def test_unknown_crs_is_not_guessed_and_bbox_must_be_valid():
    with pytest.raises(ValueError, match="transformation"):
        normalize_coordinates(12, 77, source_srid=32643)
    with pytest.raises(ValueError): validate_bbox(80, 10, 70, 20)
    assert validate_bbox(70, 10, 80, 20) == (70.0, 10.0, 80.0, 20.0)


def test_coordinate_validation_reports_unknown_crs_and_bad_geometry():
    errors = validate_spatial_record(12, 77, None, geometry_valid=False)
    assert len(errors) == 2 and all(error["code"] == "SPATIAL_VALIDATION_FAILURE" for error in errors)
    assert validate_spatial_record(12, 77, "EPSG:4326") == []


def test_spatial_queries_use_postgis_and_verified_provenance():
    class Result:
        def all(self): return []
    class Session:
        statements = []
        async def execute(self, statement):
            self.statements.append(statement)
            return Result()

    session = Session()
    result = asyncio.run(spatial_entities(session, entity_type="MINE", bbox=(70, 10, 80, 20), limit=10))
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert result == []
    assert "ST_MakeEnvelope" in sql and "ST_Intersects" in sql
    assert "canonical_records.verified_by" in sql and "source_document_id" in sql
    assert "ST_IsValid" in sql and "ST_SRID" in sql


def test_radius_spatial_query_uses_geography_metres():
    class Result:
        def all(self): return []
    class Session:
        statement = None
        async def execute(self, statement): self.statement = statement; return Result()
    session = Session()
    asyncio.run(spatial_entities(session, entity_type="BOREHOLE", point=(77, 12), radius_m=2500, limit=10))
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ST_DWithin" in sql and "geography" in sql.lower()
    assert "ST_IsValid" in sql and "ST_SRID" in sql
    assert 2500 in session.statement.compile(dialect=postgresql.dialect()).params.values()


def test_spatial_count_is_database_side_and_uses_same_verified_radius_predicate():
    class Session:
        statement = None
        async def scalar(self, statement): self.statement = statement; return 7
    session = Session()
    total = asyncio.run(count_spatial_entities(session, entity_type="MINE", point=(77, 12), radius_m=1500))
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert total == 7 and "count(trusted_mines.id)" in sql
    assert "ST_DWithin" in sql and "canonical_records.verified_by" in sql
    assert "ST_IsValid" in sql and "ST_SRID" in sql


def test_polygon_query_uses_postgis_within():
    class Result:
        def all(self): return []
    class Session:
        statement = None
        async def execute(self, statement): self.statement = statement; return Result()
    session = Session()
    polygon = '{"type":"Polygon","coordinates":[[[70,10],[80,10],[80,20],[70,20],[70,10]]]}'
    asyncio.run(spatial_entities(session, entity_type="MINE", polygon_geojson=polygon, limit=10))
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ST_GeomFromGeoJSON" in sql and "ST_Within" in sql

    asyncio.run(spatial_entities(session, entity_type="MINE", polygon_geojson=polygon,
        polygon_predicate="intersects", limit=10))
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ST_Intersects" in sql


def test_spatial_relationship_is_only_a_candidate_and_never_identity():
    class Result:
        def all(self): return []
    class Session:
        statement = None
        async def execute(self, statement): self.statement = statement; return Result()
    session = Session()
    assert asyncio.run(boreholes_near_mines(session, 500)) == []
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ST_DWithin" in sql and "canonical_records" in sql
    assert "ST_IsValid" in sql and "ST_SRID" in sql
    assert "established_relationship" not in sql


def test_distance_returns_404_for_an_unknown_or_untrusted_entity_id():
    actor = SimpleNamespace(id=uuid.uuid4(), role="ANALYST", is_active=True)

    class Result:
        def one_or_none(self): return None

    class Session:
        async def execute(self, _statement): return Result()

    async def current_user(): return actor
    async def db_session(): yield Session()
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/gis/distance?left_type=BOREHOLE&left_id={uuid.uuid4()}"
                f"&right_type=MINE&right_id={uuid.uuid4()}"
            )
        assert response.status_code == 404
        assert "Trusted spatial entity not found" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_gis_endpoints_require_authentication_and_query_coordinates_are_validated():
    with TestClient(app) as client:
        response = client.get("/api/v1/gis/layers")
        invalid = client.get("/api/v1/gis/nearby?latitude=91&longitude=0&radius_m=100")
    assert response.status_code == 401
    assert invalid.status_code == 401


def test_gis_admin_empty_layer_response_and_viewer_read_authorization():
    actor = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True)
    class Session:
        def __init__(self): self.added = []
        def add(self, row): self.added.append(row)
        async def commit(self): pass
        async def scalar(self, _statement): return 0
        async def execute(self, _statement):
            class Result:
                def all(self): return []
            return Result()
    session = Session()
    async def current_user(): return actor
    async def db_session(): yield session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            layers = client.get("/api/v1/gis/layers")
        assert layers.status_code == 200
        assert layers.json()["status"] == "INSUFFICIENT_VERIFIED_SPATIAL_DATA"
        assert layers.json()["features"] == []
        assert all(item["count"] == 0 for item in layers.json()["layers"])
        assert session.added and session.added[0].action == "GIS_LAYERS_READ"
    finally:
        app.dependency_overrides.clear()
