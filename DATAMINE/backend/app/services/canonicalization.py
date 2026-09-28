from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.gis.coordinates import normalize_coordinates, point_geometry
from app.models.canonical import CanonicalRecord, MappingProposal
from app.models.extraction import ExtractionCandidate
from app.models.identity import AuditLog, User
from app.models.trusted import (CoalBlock, GeologicalFormation, Seam, TrustedMine,
    TrustedProject, TrustedBorehole, TrustedGeologicalMeasurement,
    TrustedProductionRecord, TrustedCoordinate, CanonicalValueHistory, CanonicalDataConflict)
from app.services.structured_extraction import coordinate_validation, parse_coordinate_pair


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def resolve_identity(name: str, metadata: dict[str, Any], records: list[Any]) -> tuple[str, Any | None]:
    """Deterministic rule order: code, exact name/alias, parent, proximity, fuzzy suggestion."""
    key = normalize_name(name)
    code = str(metadata.get("code") or metadata.get("project_code") or "").strip().casefold()
    parent = str(metadata.get("parent_entity_id") or "").casefold()
    if not key and not code:
        return "UNRESOLVED", None
    if code:
        code_match = next((row for row in records if row.domain_code and row.domain_code.casefold() == code), None)
        if code_match:
            other_code_name = normalize_name(code_match.canonical_name)
            if key and key != other_code_name and key not in {normalize_name(a) for a in (code_match.aliases or [])}:
                return "POSSIBLE_MATCH", code_match
            return "MATCHED", code_match
    exact = next((row for row in records if key and (normalize_name(row.canonical_name) == key or key in {normalize_name(a) for a in (row.aliases or [])})), None)
    if exact:
        existing_parent = str((exact.attributes or {}).get("parent_entity_id") or "").casefold()
        return ("CONFLICT", exact) if parent and existing_parent and parent != existing_parent else ("MATCHED", exact)
    if parent:
        parent_match = next((row for row in records if str((row.attributes or {}).get("parent_entity_id") or "").casefold() == parent and key and SequenceMatcher(None, key, normalize_name(row.canonical_name)).ratio() >= 0.75), None)
        if parent_match:
            return "POSSIBLE_MATCH", parent_match
    try:
        lat, lon = float(metadata.get("latitude")), float(metadata.get("longitude"))
    except (TypeError, ValueError):
        lat = lon = None
    if lat is not None and lon is not None:
        for row in records:
            attrs = row.attributes or {}
            try:
                distance = ((lat-float(attrs["latitude"]))**2 + (lon-float(attrs["longitude"]))**2)**0.5
            except (KeyError, TypeError, ValueError):
                continue
            if distance <= 0.01:
                return "POSSIBLE_MATCH", row
    possible = max(((SequenceMatcher(None, key, normalize_name(row.canonical_name)).ratio(), row) for row in records if key), default=(0.0, None), key=lambda x: x[0])
    if possible[0] >= 0.88:
        return "POSSIBLE_MATCH", possible[1]
    return "NEW", None


def _checked_point(metadata: dict[str, Any]):
    if metadata.get("latitude") is None or metadata.get("longitude") is None:
        return None
    try:
        lat, lon = float(metadata["latitude"]), float(metadata["longitude"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Latitude and longitude must be numeric") from exc
    if any(result["status"] == "FAILED" for result in coordinate_validation(Decimal(str(lon)), Decimal(str(lat)))):
        raise ValueError("Latitude/longitude is outside valid ranges")
    from geoalchemy2.elements import WKTElement
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


async def canonicalize_candidate(session: AsyncSession, candidate_id: uuid.UUID, actor: User, reason: str | None = None) -> dict[str, Any]:
    candidate = await session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise LookupError("Candidate not found")
    if candidate.verification_status != "VERIFIED":
        raise ValueError("Only VERIFIED candidates can be canonicalized")
    if candidate.classification in {"UNKNOWN", "UNRESOLVED", "CONFLICT"} or candidate.mapping_status in {"POSSIBLE_MATCH", "CONFLICT", "UNRESOLVED"}:
        raise ValueError("Candidate identity requires human resolution before canonicalization")
    if not actor.is_active or actor.role not in {"ADMIN", "VERIFIER"}:
        raise PermissionError("Verifier role required")
    record = await session.scalar(select(CanonicalRecord).where(CanonicalRecord.candidate_id == candidate.id))
    if record is None or not record.verified_by or record.verified_by != actor.id:
        raise ValueError("A verification record with an authenticated verifier is required")
    if candidate.mapping_status != "APPROVED":
        raise ValueError("Candidate mapping must be explicitly approved")
    existing_result = await _existing_for_candidate(session, candidate)
    if existing_result:
        return {"candidate_id": candidate.id, "entity_type": existing_result[0], "entity_id": existing_result[1], "created": False, "idempotent": True}

    accepted = dict(record.accepted_value or {})
    name = str(accepted.get("normalized_value") or accepted.get("raw_value") or "").strip()
    if not name and candidate.classification not in {"PRODUCTION", "GEOLOGICAL_MEASUREMENT", "COORDINATE"}:
        raise ValueError("Verified candidate has no canonical name")
    key = normalize_name(name)
    now = datetime.now(timezone.utc)
    provenance = {"source_candidate_id": candidate.id, "source_document_id": candidate.document_id,
        "source_version_id": candidate.document_version_id, "source_page_id": candidate.source_page_id,
        "source_content_id": candidate.source_content_id, "source_table_id": candidate.source_table_id,
        "source_cell_id": candidate.source_cell_id, "verification_id": record.id, "verifier_id": actor.id,
        "verified_at": record.verified_at.isoformat()}
    meta = candidate.candidate_metadata or {}

    if candidate.classification in {"MINE", "PROJECT", "BOREHOLE", "COAL_BLOCK", "GEOLOGICAL_FORMATION", "SEAM"}:
        model = {"MINE": TrustedMine, "PROJECT": TrustedProject, "BOREHOLE": TrustedBorehole,
                 "COAL_BLOCK": CoalBlock, "GEOLOGICAL_FORMATION": GeologicalFormation, "SEAM": Seam}[candidate.classification]
        identity_lock_key = str(meta.get("code") or meta.get("project_code") or key or candidate.id)
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity_key, 21))"),
            {"identity_key": f"{candidate.classification}:{identity_lock_key.casefold()}"})
        peers = (await session.execute(select(model))).scalars().all()
        canonical_match = await session.scalar(select(model).where(model.canonical_entity_id == record.canonical_entity_id)) if record.canonical_entity_id else None
        identity_status, identity_match = resolve_identity(name, meta, peers)
        if canonical_match is None and identity_status in {"POSSIBLE_MATCH", "CONFLICT", "UNRESOLVED"}:
            raise ValueError(f"Identity status {identity_status} requires human resolution")
        alias_match = canonical_match or (identity_match if identity_status == "MATCHED" else None)
        attrs = {k: v for k, v in meta.items() if k in {"operator", "state", "district", "project_type", "organization", "mineral", "commodity", "description", "latitude", "longitude", "parent_entity_id"}}
        kwargs = dict(canonical_name=name, normalized_name=key, aliases=[], domain_code=meta.get("code") or meta.get("project_code"), attributes=attrs,
            source_candidate_id=candidate.id, source_document_id=candidate.document_id, source_version_id=candidate.document_version_id,
            source_page_id=candidate.source_page_id, source_content_id=candidate.source_content_id, source_table_id=candidate.source_table_id,
            source_cell_id=candidate.source_cell_id, verification_id=record.id, verifier_id=actor.id, verified_at=record.verified_at,
            canonical_entity_id=record.canonical_entity_id)
        supplied_aliases = meta.get("aliases", [])
        if isinstance(supplied_aliases, list) and all(isinstance(value, str) for value in supplied_aliases):
            kwargs["aliases"] = list(dict.fromkeys(value.strip() for value in supplied_aliases if value.strip()))
        if alias_match:
            # Exact semantic identity can reuse an existing entity. Existing values are immutable here.
            if alias_match.canonical_name != name:
                alias_match.aliases = list(dict.fromkeys([*(alias_match.aliases or []), name]))
            entity = alias_match
        else:
            if model is TrustedMine:
                kwargs.update(operator=meta.get("operator"), state=meta.get("state"), district=meta.get("district"))
                kwargs["location"] = _checked_point(meta)
            elif model is TrustedProject:
                # The relationship is added only when explicitly supplied and known to exist.
                mine_id = meta.get("mine_id")
                if mine_id:
                    mine_uuid = uuid.UUID(str(mine_id))
                    if await session.get(TrustedMine, mine_uuid) is None:
                        raise ValueError("Referenced trusted mine does not exist")
                    kwargs["mine_id"] = mine_uuid
            elif model is TrustedBorehole:
                kwargs["elevation"] = meta.get("elevation")
                kwargs["depth"] = meta.get("depth")
                if meta.get("project_id"):
                    project_uuid = uuid.UUID(str(meta["project_id"]))
                    if await session.get(TrustedProject, project_uuid) is None:
                        raise ValueError("Referenced trusted project does not exist")
                    kwargs["project_id"] = project_uuid
                kwargs["location"] = _checked_point(meta)
            elif model is CoalBlock:
                kwargs["mineral"] = meta.get("mineral")
            elif model is GeologicalFormation:
                kwargs["description"] = meta.get("description")
            elif model is Seam:
                kwargs["commodity"] = meta.get("commodity")
                if meta.get("formation_id"):
                    kwargs["formation_id"] = uuid.UUID(str(meta["formation_id"]))
            entity = model(**kwargs)
            session.add(entity)
            await session.flush()
        entity_type, entity_id = candidate.classification, entity.id
        value_fields = {"canonical_name": {"raw": candidate.raw_value, "accepted": name, "normalized": key}}
        for field_name, field_value in {**attrs, "domain_code": kwargs.get("domain_code"), "aliases": kwargs.get("aliases", [])}.items():
            if field_value is not None:
                value_fields[field_name] = {"raw": field_value, "accepted": field_value, "normalized": field_value}
    elif candidate.classification == "GEOLOGICAL_MEASUREMENT":
        if candidate.normalized_numeric_value is None or not candidate.unit:
            raise ValueError("Measurement requires a numeric value and unit")
        borehole_id = uuid.UUID(str(meta["borehole_id"])) if meta.get("borehole_id") else None
        seam_id = uuid.UUID(str(meta["seam_id"])) if meta.get("seam_id") else None
        if borehole_id and await session.get(TrustedBorehole, borehole_id) is None:
            raise ValueError("Referenced trusted borehole does not exist")
        if seam_id and await session.get(Seam, seam_id) is None:
            raise ValueError("Referenced trusted seam does not exist")
        entity = TrustedGeologicalMeasurement(measurement_type=str(meta.get("measurement_type") or "UNKNOWN"), raw_value=candidate.raw_value,
            normalized_value=candidate.normalized_numeric_value, unit=candidate.unit, depth_from=meta.get("depth_from"), depth_to=meta.get("depth_to"),
            source_candidate_id=candidate.id, source_document_id=candidate.document_id, source_version_id=candidate.document_version_id,
            source_page_id=candidate.source_page_id, source_content_id=candidate.source_content_id, source_table_id=candidate.source_table_id,
            source_cell_id=candidate.source_cell_id, verification_id=record.id, verifier_id=actor.id, verified_at=record.verified_at, measurement_metadata=meta,
            borehole_id=borehole_id, seam_id=seam_id)
        session.add(entity); await session.flush(); entity_type, entity_id = "GEOLOGICAL_MEASUREMENT", entity.id
        value_fields = {"value": {"raw": candidate.raw_value, "accepted": str(candidate.normalized_numeric_value), "normalized": str(candidate.normalized_numeric_value)}, "unit": {"raw": candidate.unit, "accepted": candidate.unit, "normalized": candidate.unit}}
    elif candidate.classification == "PRODUCTION":
        if candidate.normalized_numeric_value is None or not candidate.unit or not meta.get("reporting_period"):
            raise ValueError("Production record requires verified numeric value, unit, and reporting period")
        mine_id = uuid.UUID(str(meta["mine_id"])) if meta.get("mine_id") else None
        project_id = uuid.UUID(str(meta["project_id"])) if meta.get("project_id") else None
        if mine_id and await session.get(TrustedMine, mine_id) is None:
            raise ValueError("Referenced trusted mine does not exist")
        if project_id and await session.get(TrustedProject, project_id) is None:
            raise ValueError("Referenced trusted project does not exist")
        entity = TrustedProductionRecord(mine_id=mine_id, project_id=project_id, reporting_period=str(meta["reporting_period"]), commodity=meta.get("commodity"), production_value=candidate.normalized_numeric_value,
            production_unit=candidate.unit, source_candidate_id=candidate.id, source_document_id=candidate.document_id, source_version_id=candidate.document_version_id,
            source_page_id=candidate.source_page_id, source_content_id=candidate.source_content_id, source_table_id=candidate.source_table_id,
            source_cell_id=candidate.source_cell_id, verification_id=record.id, verifier_id=actor.id, verified_at=record.verified_at)
        session.add(entity); await session.flush(); entity_type, entity_id = "PRODUCTION", entity.id
        value_fields = {"production_value": {"raw": candidate.raw_value, "accepted": str(candidate.normalized_numeric_value), "normalized": str(candidate.normalized_numeric_value)},
            "production_unit": {"raw": candidate.unit, "accepted": candidate.unit, "normalized": candidate.unit},
            "reporting_period": {"raw": meta["reporting_period"], "accepted": meta["reporting_period"], "normalized": meta["reporting_period"]}}
        if meta.get("commodity"):
            value_fields["commodity"] = {"raw": meta["commodity"], "accepted": meta["commodity"], "normalized": meta["commodity"]}
    elif candidate.classification == "COORDINATE":
        accepted_value = record.accepted_value or {}
        accepted_pair = parse_coordinate_pair(str(accepted_value.get("normalized_value") or accepted_value.get("raw_value") or ""))
        if accepted_pair:
            longitude, latitude = accepted_pair
        else:
            latitude, longitude = meta.get("latitude"), meta.get("longitude")
        if latitude is None or longitude is None:
            raise ValueError("Coordinate candidate requires verified latitude and longitude")
        declared_crs = str(meta.get("coordinate_system") or "").strip().upper()
        if declared_crs and declared_crs not in {"EPSG:4326", "4326", "WGS84", "WGS 84"}:
            raise ValueError("Coordinate candidate must be in WGS84 (EPSG:4326)")
        normalized_coordinate = normalize_coordinates(latitude, longitude, source_srid=4326)
        latitude = Decimal(normalized_coordinate["latitude"])
        longitude = Decimal(normalized_coordinate["longitude"])
        point = point_geometry(latitude, longitude, source_srid=4326)
        entity = TrustedCoordinate(latitude=latitude, longitude=longitude, coordinate_system="EPSG:4326",
            geometry=point, source_candidate_id=candidate.id,
            source_document_id=candidate.document_id, source_version_id=candidate.document_version_id,
            source_page_id=candidate.source_page_id, source_content_id=candidate.source_content_id,
            source_table_id=candidate.source_table_id, source_cell_id=candidate.source_cell_id,
            verification_id=record.id, verifier_id=actor.id, verified_at=record.verified_at)
        session.add(entity); await session.flush(); entity_type, entity_id = "COORDINATE", entity.id
        value_fields = {"coordinates": {"raw": candidate.raw_value, "accepted": {"latitude": str(latitude), "longitude": str(longitude)}, "normalized": {"latitude": str(latitude), "longitude": str(longitude)}},
            "coordinate_system": {"raw": meta.get("coordinate_system"), "accepted": "EPSG:4326", "normalized": "EPSG:4326"}}
    else:
        raise ValueError(f"No canonical domain mapping is defined for {candidate.classification}")

    for field_name, values in value_fields.items():
        prior = await session.scalar(select(CanonicalValueHistory).where(CanonicalValueHistory.entity_type == entity_type, CanonicalValueHistory.entity_id == entity_id, CanonicalValueHistory.field_name == field_name).order_by(CanonicalValueHistory.created_at.desc()).limit(1))
        if prior and prior.normalized_value != values["normalized"]:
            prior_record = await session.get(CanonicalRecord, prior.verification_id)
            session.add(CanonicalDataConflict(entity_type=entity_type, entity_id=entity_id, field_name=field_name, value_a=prior.accepted_value,
                provenance_a={"candidate_id": str(prior.source_candidate_id), "verification_id": str(prior.verification_id),
                    "document_id": str(prior_record.source_document_id) if prior_record else None, "version_id": str(prior_record.source_version_id) if prior_record else None,
                    "page_id": str(prior_record.source_page_id) if prior_record and prior_record.source_page_id else None,
                    "content_id": str(prior_record.source_content_id) if prior_record and prior_record.source_content_id else None,
                    "table_id": str(prior_record.source_table_id) if prior_record and prior_record.source_table_id else None,
                    "cell_id": str(prior_record.source_cell_id) if prior_record and prior_record.source_cell_id else None,
                    "verifier_id": str(prior.verifier_id), "verified_at": prior.created_at.isoformat()}, value_b=values,
                provenance_b={"candidate_id": str(candidate.id), "verification_id": str(record.id), "document_id": str(candidate.document_id),
                    "version_id": str(candidate.document_version_id), "page_id": str(candidate.source_page_id) if candidate.source_page_id else None,
                    "content_id": str(candidate.source_content_id) if candidate.source_content_id else None, "table_id": str(candidate.source_table_id) if candidate.source_table_id else None,
                    "cell_id": str(candidate.source_cell_id) if candidate.source_cell_id else None, "verifier_id": str(actor.id), "verified_at": record.verified_at.isoformat()}, status="OPEN"))
            await session.flush()
            session.add(AuditLog(actor_id=actor.id, action="CONFLICT_CREATE", entity_type=entity_type, entity_id=entity_id,
                details={"field": field_name, "conflict_candidate_id": str(candidate.id)}))
            continue
        session.add(CanonicalValueHistory(entity_type=entity_type, entity_id=entity_id, field_name=field_name,
            original_value={"value": values["raw"]}, accepted_value={"value": values["accepted"]}, normalized_value=values["normalized"],
            source_candidate_id=candidate.id, verification_id=record.id, verifier_id=actor.id))
    session.add(AuditLog(actor_id=actor.id, action="CANONICALIZE", entity_type=entity_type, entity_id=entity_id,
        details={"candidate_id": str(candidate.id), "verification_id": str(record.id), "reason": reason}))
    await session.commit()
    return {"candidate_id": candidate.id, "entity_type": entity_type, "entity_id": entity_id, "created": True, "idempotent": False, "provenance": provenance}


async def _existing_for_candidate(session: AsyncSession, candidate: ExtractionCandidate):
    for model, label in [(TrustedMine, "MINE"), (TrustedProject, "PROJECT"), (TrustedBorehole, "BOREHOLE"), (CoalBlock, "COAL_BLOCK"),
        (GeologicalFormation, "GEOLOGICAL_FORMATION"), (Seam, "SEAM"), (TrustedGeologicalMeasurement, "GEOLOGICAL_MEASUREMENT"),
        (TrustedProductionRecord, "PRODUCTION"), (TrustedCoordinate, "COORDINATE")]:
        row = await session.scalar(select(model).where(model.source_candidate_id == candidate.id))
        if row is not None:
            return label, row.id
    prior_value = await session.scalar(select(CanonicalValueHistory).where(CanonicalValueHistory.source_candidate_id == candidate.id).limit(1))
    # Coordinate canonicalization is complete only when its GIS row exists.
    # A legacy history row alone must not block materializing a missing geometry.
    if prior_value and candidate.classification != "COORDINATE":
        return prior_value.entity_type, prior_value.entity_id
    return None
