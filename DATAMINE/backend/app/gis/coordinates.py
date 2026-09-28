from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from geoalchemy2.elements import WKTElement


def normalize_coordinates(latitude, longitude, *, source_srid: int = 4326) -> dict:
    if source_srid != 4326:
        raise ValueError("Coordinate transformation requires an explicitly supported source CRS")
    try:
        lat, lon = Decimal(str(latitude)), Decimal(str(longitude))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Latitude and longitude must be numeric") from exc
    if not lat.is_finite() or not lon.is_finite():
        raise ValueError("Coordinates must be finite")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Coordinates are outside WGS84 longitude/latitude ranges")
    lat_text = format(lat.normalize(), "f")
    lon_text = format(lon.normalize(), "f")
    return {"latitude": lat_text, "longitude": lon_text, "srid": 4326,
        "geometry_wkt": f"SRID=4326;POINT({lon_text} {lat_text})"}


def point_geometry(latitude, longitude, *, source_srid: int = 4326) -> WKTElement:
    normalized = normalize_coordinates(latitude, longitude, source_srid=source_srid)
    return WKTElement(normalized["geometry_wkt"], srid=4326)


def validate_bbox(west, south, east, north) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(Decimal(str(v))) for v in (west, south, east, north))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Bounding box coordinates must be numeric") from exc
    west_f, south_f, east_f, north_f = values
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Bounding box coordinates must be finite")
    if not (-180 <= west_f <= 180 and -180 <= east_f <= 180 and -90 <= south_f <= 90 and -90 <= north_f <= 90):
        raise ValueError("Bounding box is outside WGS84 ranges")
    if west_f >= east_f or south_f >= north_f:
        raise ValueError("Bounding box must have positive width and height and cannot cross the antimeridian")
    return west_f, south_f, east_f, north_f
