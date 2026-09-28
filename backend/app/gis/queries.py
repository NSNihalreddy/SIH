from __future__ import annotations

import uuid
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical import CanonicalRecord
from app.models.trusted import TrustedBorehole, TrustedCoordinate, TrustedMine


_SOURCES = {
    "MINE": (TrustedMine, TrustedMine.location),
    "BOREHOLE": (TrustedBorehole, TrustedBorehole.location),
    "VERIFIED_COORDINATE": (TrustedCoordinate, TrustedCoordinate.geometry),
}
_WGS84_GEOGRAPHY = Geography(geometry_type="Geometry", srid=4326)


def _source_query(entity_type: str):
    model, geometry = _SOURCES[entity_type]
    verification = CanonicalRecord
    return select(model, func.ST_AsGeoJSON(geometry), func.ST_IsValid(geometry)).join(
        verification, verification.id == model.verification_id).where(
            geometry.is_not(None), verification.verified_by.is_not(None),
            verification.verified_by == model.verifier_id,
            func.ST_IsValid(geometry).is_(True), func.ST_SRID(geometry) == 4326)


def _filtered_query(kind: str, *, entity_id=None, state=None, bbox=None, point=None,
                    radius_m=None, polygon_geojson=None, polygon_predicate="within"):
    model, geometry = _SOURCES[kind]
    query = _source_query(kind)
    if entity_id:
        query = query.where(model.id == entity_id)
    if state and kind == "MINE":
        query = query.where(model.state.ilike(state))
    if kind == "VERIFIED_COORDINATE":
        query = query.where(model.coordinate_system == "EPSG:4326")
    if bbox:
        west, south, east, north = bbox
        query = query.where(func.ST_Intersects(geometry, func.ST_MakeEnvelope(west, south, east, north, 4326)))
    if polygon_geojson:
        if polygon_predicate not in {"within", "intersects"}:
            raise ValueError("polygon_predicate must be within or intersects")
        polygon = func.ST_SetSRID(func.ST_GeomFromGeoJSON(polygon_geojson), 4326)
        predicate = func.ST_Within if polygon_predicate == "within" else func.ST_Intersects
        query = query.where(predicate(geometry, polygon))
    if point is not None and radius_m is not None:
        lon, lat = point
        origin = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        query = query.where(func.ST_DWithin(cast(geometry, _WGS84_GEOGRAPHY), cast(origin, _WGS84_GEOGRAPHY), radius_m))
    return query


async def count_spatial_entities(session: AsyncSession, *, entity_type: str | None = None,
                                 entity_id=None, state=None, bbox=None, point=None, radius_m=None,
                                 polygon_geojson=None, polygon_predicate="within") -> int:
    total = 0
    for kind in ([entity_type] if entity_type else list(_SOURCES)):
        if kind not in _SOURCES:
            raise ValueError(f"Unsupported spatial entity type: {kind}")
        model, _geometry = _SOURCES[kind]
        query = _filtered_query(kind, entity_id=entity_id, state=state, bbox=bbox, point=point,
            radius_m=radius_m, polygon_geojson=polygon_geojson, polygon_predicate=polygon_predicate)
        query = query.with_only_columns(func.count(model.id)).order_by(None)
        total += int(await session.scalar(query) or 0)
    return total


def _provenance(row) -> dict:
    return {field: str(getattr(row, field)) if getattr(row, field) is not None else None for field in (
        "source_document_id", "source_version_id", "source_page_id", "source_content_id",
        "source_table_id", "source_cell_id", "source_candidate_id", "verification_id", "verifier_id")}


def _feature(entity_type: str, row, geojson: str, valid: bool) -> dict:
    name = getattr(row, "canonical_name", None) or f"Verified coordinate {row.id}"
    return {"id": str(row.id), "entity_type": entity_type, "name": name,
        "geometry": __import__("json").loads(geojson),
        "properties": {"state": getattr(row, "state", None), "district": getattr(row, "district", None),
            "status": getattr(row, "status", None), "domain_code": getattr(row, "domain_code", None),
            "coordinate_system": getattr(row, "coordinate_system", "EPSG:4326"),
            "geometry_valid": bool(valid), "srid": 4326, "verification_status": "VERIFIED"},
        "provenance": _provenance(row)}


async def spatial_entities(session: AsyncSession, *, entity_type: str | None = None,
                           entity_id: uuid.UUID | None = None, state: str | None = None,
                           bbox: tuple[float, float, float, float] | None = None,
                           point: tuple[float, float] | None = None, radius_m: float | None = None,
                           polygon_geojson: str | None = None, polygon_predicate: str = "within",
                           limit: int = 100, offset: int = 0,
                           nearest_to: tuple[float, float] | None = None) -> list[dict]:
    types = [entity_type] if entity_type else list(_SOURCES)
    results = []
    for kind in types:
        if kind not in _SOURCES:
            raise ValueError(f"Unsupported spatial entity type: {kind}")
        model, _geometry = _SOURCES[kind]
        query = _filtered_query(kind, entity_id=entity_id, state=state, bbox=bbox, point=point,
            radius_m=radius_m, polygon_geojson=polygon_geojson, polygon_predicate=polygon_predicate)
        if nearest_to:
            lon, lat = nearest_to
            origin = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
            distance = func.ST_Distance(cast(_geometry, _WGS84_GEOGRAPHY), cast(origin, _WGS84_GEOGRAPHY)).label("distance_m")
            query = query.add_columns(distance).order_by(distance)
        else:
            query = query.order_by(model.id)
        query = query.offset(offset).limit(limit)
        records = (await session.execute(query)).all()
        for record in records:
            row, geojson, valid = record[:3]
            if not geojson:
                continue
            feature = _feature(kind, row, geojson, valid)
            if nearest_to:
                feature["distance_m"] = float(record[3])
                feature["distance_unit"] = "m"
            results.append(feature)
    if nearest_to:
        # Distances are computed by PostGIS and globally sorted for mixed layers.
        results.sort(key=lambda item: (item.get("distance_m", float("inf")), item["entity_type"], item["id"]))
    return results


async def distance_between_entities(session: AsyncSession, left_type: str, left_id: uuid.UUID,
                                    right_type: str, right_id: uuid.UUID) -> dict | None:
    if left_type not in {"MINE", "BOREHOLE"} or right_type not in {"MINE", "BOREHOLE"}:
        raise ValueError("Distance is available for MINE and BOREHOLE entities with trusted geometry")
    left_model, left_geom = _SOURCES[left_type]
    right_model, right_geom = _SOURCES[right_type]
    left = _source_query(left_type).where(left_model.id == left_id).subquery()
    right = _source_query(right_type).where(right_model.id == right_id).subquery()
    stmt = select(left.c.id, left.c.canonical_name, left.c.source_document_id, left.c.verification_id,
        func.ST_AsGeoJSON(left.c.location), right.c.id, right.c.canonical_name,
        right.c.source_document_id, right.c.verification_id, func.ST_AsGeoJSON(right.c.location),
        func.ST_Distance(cast(left.c.location, _WGS84_GEOGRAPHY), cast(right.c.location, _WGS84_GEOGRAPHY)))
    stmt = stmt.select_from(left.join(right, true()))
    row = (await session.execute(stmt)).one_or_none()
    if row is None: return None
    return {"distance_m": float(row[10]), "unit": "m", "function": "ST_Distance(geography, geography)",
        "left": {"id": str(row[0]), "entity_type": left_type, "name": row[1], "geometry": __import__("json").loads(row[4]),
            "provenance": {"document_id": str(row[2]), "verification_id": str(row[3])}},
        "right": {"id": str(row[5]), "entity_type": right_type, "name": row[6], "geometry": __import__("json").loads(row[9]),
            "provenance": {"document_id": str(row[7]), "verification_id": str(row[8])}}}
