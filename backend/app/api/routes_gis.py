from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_roles
from app.db.session import get_db
from app.gis.coordinates import normalize_coordinates, validate_bbox
from app.gis.queries import count_spatial_entities, distance_between_entities, spatial_entities
from app.gis.relationships import boreholes_near_mines
from app.models.identity import AuditLog, User
from app.models.analytics import AnalyticsCalculationResult
from app.models.trusted import TrustedBorehole, TrustedMine
from app.schemas.gis import GeoJSONPolygonQuery, SpatialEntityType

router = APIRouter(prefix="/api/v1/gis", tags=["trusted spatial intelligence"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Analyst = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


async def _audit(session: AsyncSession, actor: User, action: str, metadata: dict) -> None:
    session.add(AuditLog(actor_id=actor.id, action=action, entity_type="GIS", details=metadata, source="gis_api"))
    await session.commit()


async def _run_query(session, **kwargs):
    try:
        return await spatial_entities(session, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/layers")
async def layers(actor: Reader, session: AsyncSession = Depends(get_db)):
    features = await spatial_entities(session, limit=200)
    counts = {kind: await count_spatial_entities(session, entity_type=kind)
        for kind in ("MINE", "BOREHOLE", "VERIFIED_COORDINATE")}
    await _audit(session, actor, "GIS_LAYERS_READ", {"counts": counts})
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": item["id"],
        "geometry": item["geometry"], "properties": {"entity_type": item["entity_type"],
            "name": item["name"], **item["properties"], "provenance": item["provenance"]}} for item in features],
        "layers": [{"entity_type": kind, "count": counts[kind]} for kind in counts],
        "status": "OK" if features else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "limit": 200}


@router.get("/entities")
async def entities(actor: Reader, entity_type: SpatialEntityType | None = None,
                   state: str | None = Query(None, max_length=100),
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   session: AsyncSession = Depends(get_db)):
    offset = (page - 1) * page_size
    items = await _run_query(session, entity_type=entity_type, state=state, limit=page_size, offset=offset)
    total = await count_spatial_entities(session, entity_type=entity_type, state=state)
    await _audit(session, actor, "GIS_ENTITIES_READ", {"entity_type": entity_type, "state": state, "page": page, "page_size": page_size})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "total": total, "page": page, "page_size": page_size}


@router.get("/entities/{entity_type}/{entity_id}")
async def entity_detail(entity_type: SpatialEntityType, entity_id: uuid.UUID, actor: Reader,
                        session: AsyncSession = Depends(get_db)):
    items = await _run_query(session, entity_type=entity_type, entity_id=entity_id, limit=1)
    await _audit(session, actor, "GIS_ENTITY_READ", {"entity_type": entity_type, "entity_id": str(entity_id)})
    if not items:
        raise HTTPException(404, "Trusted spatial entity not found or has no verified geometry")
    return items[0]


@router.get("/nearby")
async def nearby(actor: Reader, latitude: float = Query(..., ge=-90, le=90),
                 longitude: float = Query(..., ge=-180, le=180), radius_m: float = Query(..., gt=0, le=500000),
                 entity_type: SpatialEntityType | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 session: AsyncSession = Depends(get_db)):
    try: center = normalize_coordinates(latitude, longitude)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    items = await _run_query(session, entity_type=entity_type, point=(float(center["longitude"]), float(center["latitude"])),
        radius_m=radius_m, limit=page_size, offset=(page - 1) * page_size, nearest_to=(float(center["longitude"]), float(center["latitude"])))
    total = await count_spatial_entities(session, entity_type=entity_type,
        point=(float(center["longitude"]), float(center["latitude"])), radius_m=radius_m)
    await _audit(session, actor, "GIS_NEARBY_QUERY", {"center": {"lat": center["latitude"], "lon": center["longitude"], "srid": 4326},
        "radius_m": radius_m, "result_count": len(items), "page": page})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "total": total, "page": page, "page_size": page_size, "distance_unit": "m"}


@router.get("/radius")
async def radius_alias(actor: Reader, latitude: float = Query(..., ge=-90, le=90), longitude: float = Query(..., ge=-180, le=180),
                       radius_m: float = Query(..., gt=0, le=500000),
                       entity_type: SpatialEntityType | None = None, page: int = Query(1, ge=1),
                       page_size: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_db)):
    return await nearby(actor=actor, latitude=latitude, longitude=longitude, radius_m=radius_m,
        entity_type=entity_type, page=page, page_size=page_size, session=session)


@router.get("/bbox")
async def bbox(actor: Reader, west: float, south: float, east: float, north: float,
               entity_type: SpatialEntityType | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_db)):
    try: bounds = validate_bbox(west, south, east, north)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    items = await _run_query(session, entity_type=entity_type, bbox=bounds, limit=page_size, offset=(page-1)*page_size)
    total = await count_spatial_entities(session, entity_type=entity_type, bbox=bounds)
    await _audit(session, actor, "GIS_BBOX_QUERY", {"bbox": bounds, "result_count": len(items), "page": page})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "total": total, "page": page, "page_size": page_size, "srid": 4326}


@router.get("/distance")
async def distance(actor: Analyst, left_type: SpatialEntityType, left_id: uuid.UUID,
                   right_type: SpatialEntityType, right_id: uuid.UUID,
                   session: AsyncSession = Depends(get_db)):
    try: result = await distance_between_entities(session, left_type, left_id, right_type, right_id)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Trusted spatial entity not found or missing verified valid EPSG:4326 geometry")
    calculation_id = uuid.uuid4()
    calculation = AnalyticsCalculationResult(id=calculation_id, calculation_type="SPATIAL_DISTANCE", entity_type="GIS_RELATION",
        entity_id=None, input_snapshot={"left": result["left"], "right": result["right"]},
        normalized_inputs={"left_id": str(left_id), "right_id": str(right_id), "srid": 4326, "unit": "m"},
        result_value=result["distance_m"], result_unit="m", formula_id="postgis_st_distance_geography",
        formula_version="1.0", verification_status="VERIFIED_INPUTS", validation_status="PASSED",
        metadata_json={"postgis_function": result["function"]},
        provenance={"left": result["left"]["provenance"], "right": result["right"]["provenance"]},
        created_by=actor.id)
    session.add(calculation)
    session.add(AuditLog(actor_id=actor.id, action="GIS_DISTANCE_CALCULATION", entity_type="GIS_RELATION",
        details={"calculation_id": str(calculation_id), "left_type": left_type, "left_id": str(left_id),
            "right_type": right_type, "right_id": str(right_id), "unit": "m"}, source="gis_api"))
    await session.commit()
    return {"status": "CALCULATED", "calculation_id": str(calculation_id), **result}


@router.get("/relationships")
async def relationships(actor: Analyst, radius_m: float = Query(..., gt=0, le=500000),
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                        session: AsyncSession = Depends(get_db)):
    items = await boreholes_near_mines(session, radius_m, limit=page_size)
    await _audit(session, actor, "GIS_RELATIONSHIPS_QUERY", {"radius_m": radius_m, "page": page, "result_count": len(items)})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "page": page, "page_size": page_size, "relationship_semantics": "proximity candidate only; does not establish identity or ownership"}


@router.post("/within-polygon")
async def within_polygon(request: GeoJSONPolygonQuery, actor: Analyst,
                         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                         session: AsyncSession = Depends(get_db)):
    items = await _run_query(session, entity_type=request.entity_type,
        polygon_geojson=json.dumps(request.polygon, separators=(",", ":")),
        limit=page_size, offset=(page-1)*page_size)
    total = await count_spatial_entities(session, entity_type=request.entity_type,
        polygon_geojson=json.dumps(request.polygon, separators=(",", ":")))
    await _audit(session, actor, "GIS_POLYGON_CONTAINMENT", {"result_count": len(items), "page": page})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "total": total, "page": page, "page_size": page_size, "predicate": "ST_Within"}


@router.post("/intersects")
async def intersecting_polygon(request: GeoJSONPolygonQuery, actor: Analyst,
                               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                               session: AsyncSession = Depends(get_db)):
    items = await _run_query(session, entity_type=request.entity_type,
        polygon_geojson=json.dumps(request.polygon, separators=(",", ":")), polygon_predicate="intersects",
        limit=page_size, offset=(page-1)*page_size)
    total = await count_spatial_entities(session, entity_type=request.entity_type,
        polygon_geojson=json.dumps(request.polygon, separators=(",", ":")), polygon_predicate="intersects")
    await _audit(session, actor, "GIS_INTERSECTION_QUERY", {"result_count": len(items), "page": page})
    return {"status": "OK" if items else "INSUFFICIENT_VERIFIED_SPATIAL_DATA", "items": items,
        "total": total, "page": page, "page_size": page_size, "predicate": "ST_Intersects"}
