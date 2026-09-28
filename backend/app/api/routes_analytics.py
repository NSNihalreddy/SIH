from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.deterministic import statistics_summary
from app.core.auth import require_roles
from app.db.session import get_db
from app.models.analytics import AnalyticsCalculationResult, AnalyticsValidationResult
from app.models.identity import AuditLog, User
from app.schemas.analytics import AnalyticsFilter, CalculationRequest
from app.services.analytics import (calculate_from_rows, count_verified_production, cross_source_checks,
    find_anomalies, group_production, trend_analysis, verified_production)

router = APIRouter(prefix="/api/v1/analytics", tags=["deterministic analytics"])
Reader = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST", "VIEWER"))]
Calculator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]
Validator = Annotated[User, Depends(require_roles("ADMIN", "VERIFIER", "ANALYST"))]


def _filters(model: AnalyticsFilter) -> dict:
    return model.model_dump(exclude_none=True)


async def _audit(session: AsyncSession, actor: User, action: str, target_id: uuid.UUID | None, details: dict) -> None:
    session.add(AuditLog(actor_id=actor.id, action=action, entity_type="ANALYTICS",
        entity_id=target_id, details=details, source="analytics_api"))
    await session.commit()


async def _query_records(session, filters: AnalyticsFilter, *, page: int | None = None,
                         page_size: int | None = None, record_ids=None):
    values = _filters(filters)
    return await verified_production(session, **values,
        limit=page_size, offset=(page - 1) * page_size if page and page_size else 0,
        record_ids=record_ids)


@router.get("/production")
async def production(actor: Reader, filters: AnalyticsFilter = Depends(),
                     group_by: str = Query("year", pattern="^(year|state|mine|company|commodity)$"),
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                     session: AsyncSession = Depends(get_db)):
    rows, issues = await _query_records(session, filters)
    total_records = await count_verified_production(session, **_filters(filters))
    grouped = group_production(rows, group_by)
    total = len(grouped)
    grouped = grouped[(page - 1) * page_size:page * page_size]
    await _audit(session, actor, "ANALYTICS_PRODUCTION_READ", None,
        {"filters": _filters(filters), "group_by": group_by, "page": page, "page_size": page_size, "result_count": len(rows)})
    return {"status": "OK" if rows else "INSUFFICIENT_VERIFIED_DATA", "items": grouped,
        "issues": issues[:page_size], "issue_count": len(issues), "record_count": total_records,
        "total": total, "page": page, "page_size": page_size,
        "group_by": group_by, "filters": _filters(filters)}


@router.get("/trends")
async def trends(actor: Reader, filters: AnalyticsFilter = Depends(),
                 session: AsyncSession = Depends(get_db)):
    rows, issues = await _query_records(session, filters)
    result = trend_analysis(rows)
    result["issues"] = issues
    await _audit(session, actor, "ANALYTICS_TRENDS_READ", None, {"filters": _filters(filters), "observation_count": len(result["observations"])})
    return result


@router.get("/statistics")
async def statistics(actor: Reader, filters: AnalyticsFilter = Depends(),
                     percentile: float | None = Query(None, ge=0, le=100),
                     session: AsyncSession = Depends(get_db)):
    rows, issues = await _query_records(session, filters)
    summary = statistics_summary([row["value"] for row in rows], unit=rows[0]["unit"] if rows else None, percentile=percentile)
    await _audit(session, actor, "ANALYTICS_STATISTICS_READ", None,
        {"filters": _filters(filters), "method": summary["method"], "count": summary["count"]})
    return {"status": "OK" if rows else "INSUFFICIENT_VERIFIED_DATA", "statistics": summary,
        "source_record_ids": [row["record_id"] for row in rows], "provenance": [row["provenance"] for row in rows], "issues": issues}


@router.get("/anomalies")
async def anomalies(actor: Reader, filters: AnalyticsFilter = Depends(),
                    method: str = Query("z_score", pattern="^(z_score|iqr)$"),
                    threshold: Decimal = Query(3, gt=0, le=100),
                    session: AsyncSession = Depends(get_db)):
    rows, issues = await _query_records(session, filters)
    items = find_anomalies(rows, method, threshold)
    await _audit(session, actor, "ANALYTICS_ANOMALIES_READ", None,
        {"filters": _filters(filters), "method": method, "threshold": str(threshold), "result_count": len(items)})
    return {"status": "OK" if rows else "INSUFFICIENT_VERIFIED_DATA", "classification": "STATISTICAL_ANOMALY",
        "method": method.upper(), "threshold": str(threshold), "items": items, "issues": issues,
        "interpretation": "Statistical flag only; it does not establish a real-world mining problem."}


@router.get("/validations")
async def validations(actor: Reader, validation_type: str | None = Query(None, max_length=80),
                      status: str | None = Query(None, max_length=30),
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                      session: AsyncSession = Depends(get_db)):
    stmt = select(AnalyticsValidationResult)
    if validation_type: stmt = stmt.where(AnalyticsValidationResult.validation_type == validation_type)
    if status: stmt = stmt.where(AnalyticsValidationResult.status == status)
    count_stmt = select(func.count()).select_from(AnalyticsValidationResult)
    if validation_type: count_stmt = count_stmt.where(AnalyticsValidationResult.validation_type == validation_type)
    if status: count_stmt = count_stmt.where(AnalyticsValidationResult.status == status)
    total = int(await session.scalar(count_stmt) or 0)
    stmt = stmt.order_by(AnalyticsValidationResult.created_at.desc(), AnalyticsValidationResult.id).offset((page-1)*page_size).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    await _audit(session, actor, "ANALYTICS_VALIDATIONS_READ", None,
        {"validation_type": validation_type, "status": status, "page": page, "page_size": page_size})
    return jsonable_encoder({"items": rows, "total": total, "page": page, "page_size": page_size})


@router.post("/validations/run")
async def run_validations(actor: Validator, filters: AnalyticsFilter = AnalyticsFilter(),
                          session: AsyncSession = Depends(get_db)):
    rows, issues = await _query_records(session, filters)
    if not rows and not issues:
        await _audit(session, actor, "ANALYTICS_VALIDATION_RUN", None, {"status": "INSUFFICIENT_VERIFIED_DATA", "filters": _filters(filters)})
        return {"status": "INSUFFICIENT_VERIFIED_DATA", "items": []}
    outcomes = cross_source_checks(rows, issues)
    saved = []
    for item in outcomes:
        row = AnalyticsValidationResult(validation_type=item["validation_type"], severity=item["severity"],
            status=item["status"], compared_records=item["compared_records"],
            difference=Decimal(item["difference"]) if item["difference"] is not None else None,
            tolerance=Decimal(item["tolerance"]) if item["tolerance"] is not None else None,
            provenance=item["provenance"], created_by=actor.id)
        session.add(row); saved.append(row)
    session.add(AuditLog(actor_id=actor.id, action="ANALYTICS_VALIDATION_RUN", entity_type="ANALYTICS",
        details={"filters": _filters(filters), "validation_count": len(saved)}, source="analytics_api"))
    await session.commit()
    return jsonable_encoder({"status": "COMPLETED", "items": [{**item, "id": str(row.id)} for item, row in zip(outcomes, saved)], "count": len(saved)})


@router.post("/calculate")
async def calculate(request: CalculationRequest, actor: Calculator,
                    session: AsyncSession = Depends(get_db)):
    if len(set(request.source_record_ids)) != len(request.source_record_ids):
        raise HTTPException(422, "Duplicate source record IDs are not allowed")
    rows, issues = await verified_production(session, record_ids=request.source_record_ids)
    by_id = {uuid.UUID(row["record_id"]): row for row in rows}
    ordered = [by_id[key] for key in request.source_record_ids if key in by_id]
    if len(ordered) != len(request.source_record_ids):
        raise HTTPException(422, {"status": "INSUFFICIENT_VERIFIED_DATA", "detail": "All input records must be trusted, verified production records", "invalid_record_ids": [str(key) for key in request.source_record_ids if key not in by_id]})
    if request.entity_type == "MINE" and (request.entity_id is None or any(row["mine_id"] != str(request.entity_id) for row in ordered)):
        raise HTTPException(422, "MINE calculations require an entity_id matching every source record")
    if request.entity_type == "PROJECT" and (request.entity_id is None or any(row["project_id"] != str(request.entity_id) for row in ordered)):
        raise HTTPException(422, "PROJECT calculations require an entity_id matching every source record")
    if request.entity_type == "PORTFOLIO" and request.entity_id is not None:
        raise HTTPException(422, "PORTFOLIO calculations do not accept a single entity_id")
    try:
        result = calculate_from_rows(request.calculation_type, ordered, request.years)
    except LookupError as exc:
        if "INSUFFICIENT_VERIFIED_DATA" in str(exc):
            raise HTTPException(422, {"status": "INSUFFICIENT_VERIFIED_DATA"}) from exc
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    persisted = AnalyticsCalculationResult(calculation_type=result["calculation_type"],
        entity_type=request.entity_type, entity_id=request.entity_id,
        input_snapshot=result["input_snapshot"], normalized_inputs=result["normalized_inputs"],
        result_value=Decimal(result["value"]), result_unit=result["unit"],
        formula_id=result["formula_id"], formula_version=result["formula_version"],
        verification_status="VERIFIED_INPUTS", validation_status="PASSED",
        metadata_json=result["metadata"], provenance={"source_records": result["provenance"]}, created_by=actor.id)
    session.add(persisted)
    await session.flush()
    session.add(AuditLog(actor_id=actor.id, action="ANALYTICS_CALCULATION", entity_type=request.entity_type,
        entity_id=request.entity_id, details={"calculation_type": result["calculation_type"],
            "result_id": str(persisted.id), "source_record_ids": result["source_record_ids"]}, source="analytics_api"))
    await session.commit()
    return jsonable_encoder({"status": "CALCULATED", "calculation_id": persisted.id, **result,
        "calculated_at": persisted.calculated_at, "validation_status": persisted.validation_status,
        "verification_status": persisted.verification_status})
