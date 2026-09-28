from __future__ import annotations

from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical import CanonicalRecord
from app.models.trusted import TrustedBorehole, TrustedMine

_WGS84_GEOGRAPHY = Geography(geometry_type="Geometry", srid=4326)


async def boreholes_near_mines(session: AsyncSession, radius_m: float, *, limit: int = 100) -> list[dict]:
    if radius_m <= 0 or radius_m > 500000:
        raise ValueError("radius_m must be greater than zero and at most 500000")
    borehole, mine = TrustedBorehole, TrustedMine
    borehole_verification = aliased(CanonicalRecord)
    mine_verification = aliased(CanonicalRecord)
    query = (select(borehole.id, borehole.canonical_name, borehole.source_document_id, borehole.source_page_id,
        borehole.verification_id, mine.id, mine.canonical_name, mine.source_document_id, mine.source_page_id,
        mine.verification_id, func.ST_Distance(cast(borehole.location, _WGS84_GEOGRAPHY), cast(mine.location, _WGS84_GEOGRAPHY)))
        .join(borehole_verification, (borehole_verification.id == borehole.verification_id) &
            borehole_verification.verified_by.is_not(None) & (borehole_verification.verified_by == borehole.verifier_id))
        .join(mine, func.ST_DWithin(cast(borehole.location, _WGS84_GEOGRAPHY), cast(mine.location, _WGS84_GEOGRAPHY), radius_m))
        .join(mine_verification, (mine_verification.id == mine.verification_id) &
            mine_verification.verified_by.is_not(None) & (mine_verification.verified_by == mine.verifier_id))
        .where(borehole.location.is_not(None), mine.location.is_not(None),
            func.ST_IsValid(borehole.location).is_(True), func.ST_IsValid(mine.location).is_(True),
            func.ST_SRID(borehole.location) == 4326, func.ST_SRID(mine.location) == 4326)
        .order_by(borehole.id, mine.id).limit(limit))
    rows = (await session.execute(query)).all()
    return [{"relation_type": "SPATIAL_CANDIDATE", "established_relationship": False,
        "borehole": {"id": str(row[0]), "name": row[1], "provenance": {"document_id": str(row[2]),
            "page_id": str(row[3]) if row[3] else None, "verification_id": str(row[4])}},
        "mine": {"id": str(row[5]), "name": row[6], "provenance": {"document_id": str(row[7]),
            "page_id": str(row[8]) if row[8] else None, "verification_id": str(row[9])}},
        "distance_m": float(row[10]), "unit": "m"} for row in rows]
