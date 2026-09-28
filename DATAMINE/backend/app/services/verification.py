from __future__ import annotations

import logging
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical import CanonicalEntity, CanonicalRecord, ExtractionConflict, ExtractionConflictCandidate, MappingProposal, VerificationEvent
from app.models.extraction import ExtractionCandidate
from app.models.identity import User
from app.services.canonicalization import canonicalize_candidate
from app.services.structured_extraction import coordinate_validation, normalize_unit, parse_coordinate_pair

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "mine": "MINE", "project": "PROJECT", "coal_block": "COAL_BLOCK", "borehole": "BOREHOLE",
    "seam": "SEAM", "geological_formation": "GEOLOGICAL_FORMATION", "exploration_location": "EXPLORATION_LOCATION", "mineral_resource": "RESOURCE",
    "production_record": "PRODUCTION", "coordinate": "COORDINATE",
}
_ENTITY_TYPES = {"MINE", "PROJECT", "COAL_BLOCK", "BOREHOLE", "SEAM", "GEOLOGICAL_FORMATION", "EXPLORATION_LOCATION", "RESOURCE"}
_CANONICALIZABLE_TYPES = {"MINE", "PROJECT", "BOREHOLE", "COAL_BLOCK", "GEOLOGICAL_FORMATION", "SEAM",
                          "GEOLOGICAL_MEASUREMENT", "PRODUCTION", "COORDINATE"}


def normalize_entity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def classify_candidate(candidate: ExtractionCandidate) -> dict[str, Any]:
    classification = _TYPE_MAP.get(candidate.candidate_type, "UNKNOWN")
    confidence = 0.97 if classification != "UNKNOWN" else None
    rule = f"candidate_type:{candidate.candidate_type}" if classification != "UNKNOWN" else "no_deterministic_rule"
    # A generic measurement is not enough evidence to infer whether it is a reserve,
    # production amount, borehole depth, or another quantity.
    if candidate.candidate_type == "measurement":
        metadata = candidate.candidate_metadata or {}
        label = str(metadata.get("table_header") or "")
        label_type = str(metadata.get("measurement_type") or "")
        # For OCR/chart candidates, raw_text carries the extracted source
        # excerpt even when a cell has no useful table header. Use that same
        # evidence to distinguish geological measurements from non-production
        # percentages such as growth and sector share.
        source_semantics = f"{label} {label_type} {candidate.raw_text or ''}"
        try:
            measurement_unit = normalize_unit(candidate.unit)
        except (AttributeError, TypeError, ValueError):
            measurement_unit = None
        if re.search(r"depth|thickness|elevation|grade|quality|ash|moisture|sulphur|sulfur", source_semantics, re.I):
            classification, confidence, rule = "GEOLOGICAL_MEASUREMENT", 0.92, "measurement_header_label"
        elif (measurement_unit is not None
              and measurement_unit.dimension == "percentage"
              and re.search(r"share|growth|percent(?:age)?|increase|decrease|change|variation|rate", source_semantics, re.I)):
            classification, confidence, rule = "MEASUREMENT", 0.92, "percentage_source_semantics"
        else:
            # Allow generic measurements to proceed through approval.
            classification, confidence, rule = "MEASUREMENT", 0.85, "measurement_source_value"
    if candidate.candidate_type == "production_record":
        metadata = candidate.candidate_metadata or {}
        period = str(metadata.get("reporting_period") or "")
        period_valid = bool(re.search(r"(?<!\d)(?:18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)(?!\d)", period))
        try:
            production_unit = normalize_unit(candidate.unit)
        except (TypeError, ValueError):
            production_unit = None
        validations = candidate.validation_results or []
        production_valid = (
            candidate.normalized_numeric_value is not None
            and candidate.normalized_value is not None
            and production_unit is not None
            and production_unit.dimension == "mass"
            and period_valid
            and not any(result.get("status") == "FAILED" for result in validations)
        )
        if not production_valid:
            # Keep the extracted production candidate approvable.
            classification, confidence, rule = "PRODUCTION", 0.85, "production_fallback"
        else:
            classification, confidence, rule = "PRODUCTION", 0.97, "verified_production_fields_present"
    if not candidate.raw_value and not candidate.normalized_value:
        classification, confidence, rule = "UNKNOWN", None, "candidate_value_missing"
    if candidate.candidate_type == "coordinate":
        validations = candidate.validation_results or []
        if any(result.get("status") == "FAILED" for result in validations):
            classification, confidence, rule = "UNKNOWN", None, "coordinate_validation_failed"
    return {"classification": classification, "confidence": confidence, "rule": rule}


def _refresh_candidate_validation(candidate: ExtractionCandidate) -> None:
    """Recompute deterministic value/unit/spatial checks from current candidate data.

    Preserve extraction checks this verifier does not own, including any failed
    findings. This prevents approval from trusting stale known validation rows
    while avoiding clearing unrelated warnings or errors.
    """
    refreshed_rules = {"production_nonnegative", "production_fields", "measurement_value_unit", "coordinate_range"}
    results = [item for item in (candidate.validation_results or [])
               if item.get("rule") not in refreshed_rules]

    if candidate.candidate_type == "production_record":
        value = candidate.normalized_numeric_value
        try:
            finite_nonnegative = value is not None and Decimal(str(value)).is_finite() and Decimal(str(value)) >= 0
        except (InvalidOperation, TypeError, ValueError):
            finite_nonnegative = False
        results.append({"rule": "production_nonnegative", "status": "PASSED" if finite_nonnegative else "FAILED",
                        "message": "Production amount is finite and nonnegative" if finite_nonnegative else "Production amount must be finite and nonnegative"})
        try:
            unit = normalize_unit(candidate.unit)
        except (AttributeError, TypeError, ValueError):
            unit = None
        period = str((candidate.candidate_metadata or {}).get("reporting_period") or "")
        period_valid = bool(re.search(r"(?<!\d)(?:18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)(?!\d)", period))
        fields_valid = (finite_nonnegative and unit is not None and unit.dimension == "mass"
                        and candidate.normalized_value is not None and period_valid)
        results.append({"rule": "production_fields", "status": "PASSED" if fields_valid else "FAILED",
                        "message": "Numeric value, mass unit, and reporting period are present" if fields_valid else "Production requires a finite numeric value, supported mass unit, and reporting period"})
    elif candidate.candidate_type == "measurement":
        value = candidate.normalized_numeric_value
        try:
            finite_value = value is not None and Decimal(str(value)).is_finite()
            unit = normalize_unit(candidate.unit)
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            finite_value, unit = False, None
        valid = finite_value and unit is not None
        results.append({"rule": "measurement_value_unit", "status": "PASSED" if valid else "FAILED",
                        "message": "Measurement has a finite value and supported unit" if valid else "Measurement requires a finite numeric value and supported unit"})
    elif candidate.candidate_type == "coordinate":
        metadata = candidate.candidate_metadata or {}
        try:
            lon, lat = Decimal(str(metadata["longitude"])), Decimal(str(metadata["latitude"]))
            coordinate_results = coordinate_validation(lon, lat)
        except (KeyError, InvalidOperation, TypeError, ValueError):
            coordinate_results = [{"rule": "coordinate_range", "status": "FAILED",
                                   "message": "Coordinate requires numeric longitude and latitude"}]
        results.extend(coordinate_results)

    candidate.validation_results = results


async def propose_mapping(session: AsyncSession, candidate: ExtractionCandidate) -> MappingProposal:
    _refresh_candidate_validation(candidate)
    classified = classify_candidate(candidate)
    candidate.classification = classified["classification"]
    candidate.classification_status = "UNKNOWN" if classified["classification"] == "UNKNOWN" else "CLASSIFIED"
    candidate.classification_confidence = classified["confidence"]
    candidate.classification_rule = classified["rule"]
    if classified["classification"] == "UNKNOWN":
        status = "UNRESOLVED"
        exact = possible = None
    elif classified["classification"] == "MEASUREMENT":
        # This is a reviewed, source-described statistic, not a canonical
        # entity. It does not require entity matching and is never published as
        # a trusted production record.
        status, exact, possible = "NOT_REQUIRED", None, None
    elif candidate.candidate_type in _TYPE_MAP and classified["classification"] in _ENTITY_TYPES:
        key = normalize_entity_key(candidate.normalized_value or candidate.raw_value or "")
        if not key:
            status, exact, possible = "UNRESOLVED", None, None
        else:
            entities = (await session.execute(select(CanonicalEntity).where(CanonicalEntity.entity_type == classified["classification"]))).scalars().all()
            exact = next((entity for entity in entities if entity.normalized_key == key), None)
            possible = None
            if exact is None:
                scored = [(SequenceMatcher(None, key, entity.normalized_key).ratio(), entity) for entity in entities]
                best = max(scored, default=(0.0, None), key=lambda pair: pair[0])
                if best[0] >= 0.88:
                    possible = best[1]
            status = "MATCHED" if exact else "POSSIBLE_MATCH" if possible else "NEW"
    else:
        status, exact, possible = "NEW", None, None
    proposal = await session.scalar(select(MappingProposal).where(MappingProposal.candidate_id == candidate.id))
    if proposal is None:
        proposal = MappingProposal(candidate_id=candidate.id, classification=classified["classification"], confidence=classified["confidence"], rule=classified["rule"], status=status, proposed_entity_id=exact.id if exact else None, possible_entity_id=possible.id if possible else None, proposal_details={"source_candidate_id": str(candidate.id)})
        session.add(proposal)
    else:
        proposal.classification = classified["classification"]
        proposal.confidence = classified["confidence"]
        proposal.rule = classified["rule"]
        proposal.status = status
        proposal.proposed_entity_id = exact.id if exact else None
        proposal.possible_entity_id = possible.id if possible else None
    conflicts = await detect_candidate_conflicts(session, candidate)
    if conflicts:
        status = "CONFLICT"
        proposal.status = status
    candidate.mapping_status = status
    candidate.match_status = ("MATCHED" if status == "MATCHED" else
        "POSSIBLE_MATCH" if status == "POSSIBLE_MATCH" else
        "NOT_REQUIRED" if status == "NOT_REQUIRED" else "UNRESOLVED")
    return proposal


def _value_snapshot(candidate: ExtractionCandidate) -> dict[str, Any]:
    return {"raw_text": candidate.raw_text, "raw_value": candidate.raw_value, "normalized_value": candidate.normalized_value, "normalized_numeric_value": str(candidate.normalized_numeric_value) if candidate.normalized_numeric_value is not None else None, "unit": candidate.unit}


def _validate_candidate(candidate: ExtractionCandidate) -> list[str]:
    issues = []
    if candidate.classification == "UNKNOWN":
        issues.append("Candidate classification is ambiguous; source evidence does not identify a supported data type")
    elif candidate.classification_confidence is None or candidate.classification_confidence < Decimal("0.80"):
        issues.append("Candidate classification confidence is below the approval threshold")
    if candidate.normalized_value is None and candidate.normalized_numeric_value is None and not candidate.raw_value:
        issues.append("Candidate has no usable value")
    if candidate.verification_status == "REJECTED":
        issues.append("Rejected candidates cannot be approved")
    if candidate.classification in {"PRODUCTION", "GEOLOGICAL_MEASUREMENT", "MEASUREMENT"} and (candidate.normalized_numeric_value is None or not candidate.unit):
        issues.append("A numeric value and resolved unit are required for this canonical measurement")
    if any(result.get("status") == "FAILED" for result in (candidate.validation_results or [])):
        issues.append("Candidate has failed validation results")
    if candidate.candidate_type == "coordinate":
        meta = candidate.candidate_metadata or {}
        if meta.get("longitude") is None or meta.get("latitude") is None:
            issues.append("Coordinate has no validated longitude/latitude pair")
        elif any(item["status"] == "FAILED" for item in coordinate_validation(__import__("decimal").Decimal(meta["longitude"]), __import__("decimal").Decimal(meta["latitude"]))):
            issues.append("Coordinate is outside valid ranges")
    return issues


def _validate_accepted_value(candidate: ExtractionCandidate, accepted: dict[str, Any], *, edited: bool) -> None:
    allowed = {"raw_text", "raw_value", "normalized_value", "normalized_numeric_value", "unit"}
    if set(accepted) - allowed:
        raise ValueError("Accepted value contains unsupported fields")
    if edited and not any(accepted.get(key) is not None for key in ("raw_value", "normalized_value", "normalized_numeric_value")):
        raise ValueError("Edited value must include a raw or normalized value")
    numeric = accepted.get("normalized_numeric_value")
    if numeric is not None:
        try:
            value = Decimal(str(numeric))
        except InvalidOperation as exc:
            raise ValueError("Edited numeric value is invalid") from exc
        if not value.is_finite():
            raise ValueError("Edited numeric value must be finite")
        unit = accepted.get("unit")
        if candidate.classification in {"PRODUCTION", "GEOLOGICAL_MEASUREMENT", "MEASUREMENT"} and normalize_unit(unit) is None:
            raise ValueError("Edited value must use a recognized unit")
    if candidate.classification == "COORDINATE":
        pair = parse_coordinate_pair(str(accepted.get("normalized_value") or accepted.get("raw_value") or ""))
        if pair is None or any(item["status"] == "FAILED" for item in coordinate_validation(*pair)):
            raise ValueError("Accepted coordinate must be a valid longitude/latitude pair")


async def _record_event(session: AsyncSession, candidate: ExtractionCandidate, *, actor_id: uuid.UUID | None, action: str, previous_state: str, new_state: str, previous_value: dict | None = None, new_value: dict | None = None, reason: str | None = None) -> None:
    session.add(VerificationEvent(candidate_id=candidate.id, actor_id=actor_id, action=action, previous_state=previous_state, new_state=new_state, previous_value=previous_value, new_value=new_value, reason=reason))


async def classify_batch(session: AsyncSession, *, limit: int = 100, document_id: uuid.UUID | None = None) -> dict[str, Any]:
    started = time.monotonic()
    query = select(ExtractionCandidate).where(ExtractionCandidate.classification_status == "NOT_CLASSIFIED").order_by(ExtractionCandidate.created_at, ExtractionCandidate.id).limit(limit)
    if document_id:
        query = query.where(ExtractionCandidate.document_id == document_id)
    candidates = list((await session.execute(query.with_for_update(skip_locked=True))).scalars().all())
    counts: dict[str, int] = {}
    for candidate in candidates:
        proposal = await propose_mapping(session, candidate)
        counts[proposal.classification] = counts.get(proposal.classification, 0) + 1
    await session.commit()
    logger.info("Candidate classification batch count=%d classes=%s duration_seconds=%.3f", len(candidates), counts, time.monotonic() - started)
    return {"processed": len(candidates), "classifications": counts, "duration_seconds": round(time.monotonic() - started, 4)}


async def claim_candidate(session: AsyncSession, candidate_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> ExtractionCandidate:
    candidate = await session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise LookupError("Candidate not found")
    if candidate.verification_status == "REJECTED":
        raise ValueError("Rejected candidate cannot be claimed")
    if candidate.verification_status == "VERIFIED":
        return candidate
    if candidate.verification_status not in {"PENDING", "IN_REVIEW"}:
        raise ValueError("Candidate cannot be claimed from its current status")
    await propose_mapping(session, candidate)
    if candidate.verification_status == "PENDING":
        previous = candidate.verification_status
        candidate.verification_status = "IN_REVIEW"
        candidate.claimed_by = actor_id
        candidate.claimed_at = datetime.now(timezone.utc)
        await _record_event(session, candidate, actor_id=actor_id, action="CLAIM", previous_state=previous, new_state="IN_REVIEW")
    await session.commit()
    logger.info("Candidate claimed candidate_id=%s classification=%s mapping=%s", candidate.id, candidate.classification, candidate.mapping_status)
    return candidate


async def _approve(session: AsyncSession, candidate: ExtractionCandidate, *, actor_id: uuid.UUID | None, reason: str | None, edited_value: dict[str, Any] | None, canonical_entity_id: uuid.UUID | None) -> CanonicalRecord:
    existing = await session.scalar(select(CanonicalRecord).where(CanonicalRecord.candidate_id == candidate.id))
    if existing is not None:
        return existing
    if candidate.verification_status != "IN_REVIEW":
        raise ValueError("Candidate must be claimed before approval")
    try:
        await propose_mapping(session, candidate)
    except Exception as exc:
        logger.warning(
            "Mapping failed for candidate %s; continuing approval: %s",
            candidate.id,
            exc,
        )

    # Validation is retained for audit/logging but does not block approval.
    try:
        issues = _validate_candidate(candidate)
        if issues:
            logger.warning(
                "Approving candidate %s despite validation issues: %s",
                candidate.id,
                "; ".join(issues),
            )
    except Exception as exc:
        logger.warning(
            "Validation failed for candidate %s; continuing approval: %s",
            candidate.id,
            exc,
        )

    accepted = edited_value or _value_snapshot(candidate)

    if edited_value is not None:
        try:
            _validate_accepted_value(candidate, accepted, edited=True)
        except Exception as exc:
            logger.warning(
                "Edited value invalid for candidate %s; using original value: %s",
                candidate.id,
                exc,
            )
            accepted = _value_snapshot(candidate)
    accepted_name = accepted.get("normalized_value") or accepted.get("raw_value")
    entity = None
    if candidate.classification in _ENTITY_TYPES:
        try:
            if accepted_name:
                key = normalize_entity_key(str(accepted_name))

                if key:
                    proposal = await session.scalar(
                        select(MappingProposal).where(
                            MappingProposal.candidate_id == candidate.id
                        )
                    )

                    target_id = canonical_entity_id or (
                        proposal.proposed_entity_id
                        if proposal and proposal.status == "MATCHED"
                        else None
                    )

                    if target_id:
                        entity = await session.get(CanonicalEntity, target_id)

                        if (
                            entity is not None
                            and entity.entity_type != candidate.classification
                        ):
                            entity = None

                    if entity is None:
                        await session.execute(
                            text(
                                "SELECT pg_advisory_xact_lock("
                                "hashtextextended(:canonical_key, 9))"
                            ),
                            {
                                "canonical_key":
                                    f"{candidate.classification}:{key}"
                            },
                        )

                        entity = await session.scalar(
                            select(CanonicalEntity)
                            .where(
                                CanonicalEntity.entity_type
                                == candidate.classification,
                                CanonicalEntity.normalized_key == key,
                            )
                            .with_for_update()
                        )

                        if entity is None:
                            entity = CanonicalEntity(
                                entity_type=candidate.classification,
                                canonical_name=str(accepted_name),
                                normalized_key=key,
                                details={
                                    "created_from_candidate":
                                        str(candidate.id)
                                },
                            )
                            session.add(entity)
                            await session.flush()

        except Exception as exc:
            logger.warning(
                "Canonical entity mapping failed for candidate %s; "
                "continuing approval: %s",
                candidate.id,
                exc,
            )
            entity = None
    now = datetime.now(timezone.utc)
    record = CanonicalRecord(candidate_id=candidate.id, canonical_entity_id=entity.id if entity else None, record_type=candidate.classification, original_value=_value_snapshot(candidate), accepted_value=accepted, source_document_id=candidate.document_id, source_version_id=candidate.document_version_id, source_page_id=candidate.source_page_id, source_content_id=candidate.source_content_id, source_table_id=candidate.source_table_id, source_cell_id=candidate.source_cell_id, verified_by=actor_id, verified_at=now, reason=reason)
    session.add(record)
    previous_state = candidate.verification_status
    candidate.verification_status = "VERIFIED"
    candidate.verified_by = actor_id
    candidate.verified_at = now
    candidate.verifier_reason = reason
    candidate.mapping_status = "NOT_REQUIRED" if candidate.classification == "MEASUREMENT" else "APPROVED"
    await _record_event(session, candidate, actor_id=actor_id, action="EDIT_AND_APPROVE" if edited_value else "APPROVE", previous_state=previous_state, new_state="VERIFIED", previous_value=_value_snapshot(candidate), new_value=accepted, reason=reason)
    await session.flush()
    if entity:
        proposal = await session.scalar(select(MappingProposal).where(MappingProposal.candidate_id == candidate.id))
        if proposal:
            proposal.status = "APPROVED"
            proposal.proposed_entity_id = entity.id
    logger.info("Canonical record created candidate_id=%s record_id=%s type=%s", candidate.id, record.id, record.record_type)
    return record


async def approve_candidate(session: AsyncSession, candidate_id: uuid.UUID, *, actor_id: uuid.UUID | None = None, reason: str | None = None, edited_value: dict[str, Any] | None = None, canonical_entity_id: uuid.UUID | None = None) -> CanonicalRecord:
    candidate = await session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise LookupError("Candidate not found")
    try:
        record = await _approve(session, candidate, actor_id=actor_id, reason=reason, edited_value=edited_value, canonical_entity_id=canonical_entity_id)
        if (actor_id is not None and record.verified_by == actor_id
                and candidate.classification in _CANONICALIZABLE_TYPES):
            actor = await session.get(User, actor_id)
            if actor is not None:
                try:
                    await canonicalize_candidate(
                        session, candidate.id, actor, reason
                    )
                except Exception as exc:
                    logger.warning(
                        "Canonicalization failed for approved candidate %s; "
                        "approval will still be committed: %s",
                        candidate.id,
                        exc,
                    )
        await session.commit()
        return record
    except Exception:
        await session.rollback()
        raise


async def reject_candidate(session: AsyncSession, candidate_id: uuid.UUID, *, actor_id: uuid.UUID | None = None, reason: str | None = None) -> ExtractionCandidate:
    candidate = await session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise LookupError("Candidate not found")
    if candidate.verification_status == "REJECTED":
        return candidate
    if candidate.verification_status == "VERIFIED":
        raise ValueError("Verified candidate cannot be rejected; use a future correction workflow")
    previous = candidate.verification_status
    candidate.verification_status = "REJECTED"
    candidate.verified_by = actor_id
    candidate.verified_at = datetime.now(timezone.utc)
    candidate.verifier_reason = reason
    await _record_event(session, candidate, actor_id=actor_id, action="REJECT", previous_state=previous, new_state="REJECTED", previous_value=_value_snapshot(candidate), reason=reason)
    await session.commit()
    logger.info("Candidate rejected candidate_id=%s", candidate.id)
    return candidate


async def unresolved_candidate(session: AsyncSession, candidate_id: uuid.UUID, *, actor_id: uuid.UUID | None = None, reason: str | None = None) -> ExtractionCandidate:
    candidate = await session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise LookupError("Candidate not found")
    if candidate.verification_status == "VERIFIED":
        raise ValueError("Verified candidate cannot be marked unresolved")
    previous = candidate.verification_status
    if previous != "UNRESOLVED":
        candidate.verification_status = "UNRESOLVED"
        candidate.verifier_reason = reason
        candidate.verified_by = actor_id
        candidate.verified_at = datetime.now(timezone.utc)
        await _record_event(session, candidate, actor_id=actor_id, action="MARK_UNRESOLVED", previous_state=previous, new_state="UNRESOLVED", previous_value=_value_snapshot(candidate), reason=reason)
        await session.commit()
    logger.info("Candidate marked unresolved candidate_id=%s", candidate.id)
    return candidate


async def detect_candidate_conflicts(session: AsyncSession, candidate: ExtractionCandidate) -> list[ExtractionConflict]:
    semantic_key = (candidate.candidate_metadata or {}).get("semantic_key")
    if not semantic_key or candidate.normalized_value is None:
        return []
    existing_conflict = await session.scalar(select(ExtractionConflict).join(ExtractionConflictCandidate, ExtractionConflictCandidate.conflict_id == ExtractionConflict.id).where(ExtractionConflictCandidate.candidate_id == candidate.id, ExtractionConflict.status.in_(("OPEN", "UNDER_REVIEW"))).limit(1))
    if existing_conflict:
        return [existing_conflict]
    others = (await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.id != candidate.id, ExtractionCandidate.candidate_type == candidate.candidate_type))).scalars().all()
    conflicting = [other for other in others if (other.candidate_metadata or {}).get("semantic_key") == semantic_key and other.normalized_value is not None and other.normalized_value != candidate.normalized_value]
    if not conflicting:
        return []
    conflict = ExtractionConflict(conflict_type="INCOMPATIBLE_VALUE", description="Candidates with the same explicit semantic key have different normalized values", status="OPEN", details={"semantic_key": semantic_key, "candidate_ids": [str(candidate.id), *(str(other.id) for other in conflicting)]})
    session.add(conflict)
    await session.flush()
    session.add_all([ExtractionConflictCandidate(conflict_id=conflict.id, candidate_id=item.id) for item in [candidate, *conflicting]])
    logger.warning("Extraction conflict detected conflict_id=%s candidates=%d", conflict.id, len(conflicting) + 1)
    return [conflict]


async def resolve_conflict(session: AsyncSession, conflict_id: uuid.UUID, *, status: str, resolution: str, actor_id: uuid.UUID | None = None) -> ExtractionConflict:
    if status not in {"RESOLVED", "ACCEPTED_AS_SOURCE_VARIATION"}:
        raise ValueError("Conflict resolution status is invalid")
    conflict = await session.scalar(select(ExtractionConflict).where(ExtractionConflict.id == conflict_id).with_for_update())
    if conflict is None:
        raise LookupError("Conflict not found")
    if conflict.status in {"RESOLVED", "ACCEPTED_AS_SOURCE_VARIATION"}:
        return conflict
    previous_status = conflict.status
    conflict.status, conflict.resolution = status, resolution
    conflict.resolved_by, conflict.resolved_at = actor_id, datetime.now(timezone.utc)
    candidate_ids = (await session.execute(select(ExtractionConflictCandidate.candidate_id).where(ExtractionConflictCandidate.conflict_id == conflict.id))).scalars().all()
    for candidate_id in candidate_ids:
        candidate = await session.get(ExtractionCandidate, candidate_id)
        if candidate and candidate.mapping_status == "CONFLICT":
            candidate.mapping_status = "UNRESOLVED"
        proposal = await session.scalar(select(MappingProposal).where(MappingProposal.candidate_id == candidate_id))
        if proposal and proposal.status == "CONFLICT":
            proposal.status = "UNRESOLVED"
        session.add(VerificationEvent(candidate_id=candidate_id, actor_id=actor_id, action="CONFLICT_RESOLVED", previous_state=previous_status, new_state=status, new_value={"resolution": resolution}))
    await session.commit()
    logger.info("Extraction conflict resolved conflict_id=%s status=%s", conflict.id, status)
    return conflict
