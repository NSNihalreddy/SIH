import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


SpatialEntityType = Literal["MINE", "BOREHOLE", "VERIFIED_COORDINATE"]


class NearbyQuery(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=500000)
    entity_type: SpatialEntityType | None = None
    state: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class GeoJSONPolygonQuery(BaseModel):
    polygon: dict
    entity_type: SpatialEntityType | None = None

    @model_validator(mode="after")
    def validate_polygon(self):
        if self.polygon.get("type") != "Polygon" or not isinstance(self.polygon.get("coordinates"), list):
            raise ValueError("polygon must be a GeoJSON Polygon")
        if len(str(self.polygon)) > 100_000:
            raise ValueError("polygon payload is too large")
        rings = self.polygon["coordinates"]
        if not rings:
            raise ValueError("polygon must include at least one ring")
        from app.gis.coordinates import normalize_coordinates
        for ring in rings:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("each polygon ring must have at least four positions and be closed")
            for position in ring:
                if not isinstance(position, (list, tuple)) or len(position) != 2:
                    raise ValueError("polygon positions must contain longitude and latitude only")
                normalize_coordinates(position[1], position[0])
        return self
