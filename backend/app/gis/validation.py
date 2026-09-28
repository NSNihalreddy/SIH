from __future__ import annotations

from app.gis.coordinates import normalize_coordinates


def validate_spatial_record(latitude, longitude, coordinate_system: str | None, *, geometry_valid: bool = True) -> list[dict]:
    issues = []
    try:
        if coordinate_system == "EPSG:4326":
            normalize_coordinates(latitude, longitude, source_srid=4326)
        elif not coordinate_system:
            normalize_coordinates(latitude, longitude, source_srid=4326)
        else:
            normalize_coordinates(latitude, longitude, source_srid=-1)
    except ValueError as exc:
        issues.append({"code": "SPATIAL_VALIDATION_FAILURE", "detail": str(exc)})
    if not coordinate_system:
        issues.append({"code": "SPATIAL_VALIDATION_FAILURE", "detail": "Coordinate reference system is missing"})
    elif coordinate_system != "EPSG:4326":
        issues.append({"code": "SPATIAL_VALIDATION_FAILURE", "detail": "Only explicit EPSG:4326 coordinates are queryable in this layer"})
    if not geometry_valid:
        issues.append({"code": "SPATIAL_VALIDATION_FAILURE", "detail": "Geometry is invalid"})
    return issues
