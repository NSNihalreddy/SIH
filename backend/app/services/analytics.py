from __future__ import annotations

import re
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.deterministic import (
    cagr, detect_anomalies, growth_percentage, production_difference,
    production_share, statistics_summary,
)
from app.analytics.units import normalize_value
from app.models.canonical import CanonicalRecord
from app.models.trusted import TrustedMine, TrustedProductionRecord, TrustedProject


_YEAR = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)(?!\d)")
_YEAR_SQL = r"(18[0-9]{2}|19[0-9]{2}|20[0-9]{2}|21[0-9]{2}|2200)"


def _verified_query():
    record = TrustedProductionRecord
    return (select(record, TrustedMine, TrustedProject)
        .join(CanonicalRecord, CanonicalRecord.id == record.verification_id)
        .outerjoin(TrustedProject, TrustedProject.id == record.project_id)
        .outerjoin(TrustedMine, TrustedMine.id == func.coalesce(record.mine_id, TrustedProject.mine_id))
        .where(record.verification_status == "VERIFIED", CanonicalRecord.verified_by.is_not(None),
               CanonicalRecord.verified_by == record.verifier_id))


def _apply_filters(query, *, entity_type=None, entity_id=None, year=None, year_from=None,
                   year_to=None, state=None, commodity=None, unit=None, record_ids=None):
    record = TrustedProductionRecord
    year_expr = cast(func.substring(record.reporting_period, _YEAR_SQL), Integer)
    if entity_type == "MINE": query = query.where(record.mine_id == entity_id)
    if entity_type == "PROJECT": query = query.where(record.project_id == entity_id)
    if entity_type == "PORTFOLIO" and entity_id: query = query.where(record.mine_id == entity_id)
    if year is not None: query = query.where(year_expr == year)
    if year_from is not None: query = query.where(year_expr >= year_from)
    if year_to is not None: query = query.where(year_expr <= year_to)
    if state: query = query.where(TrustedMine.state.ilike(state))
    if commodity: query = query.where(record.commodity.ilike(commodity))
    if record_ids is not None: query = query.where(record.id.in_(record_ids))
    return query


def reporting_year(period: str) -> int | None:
    match = _YEAR.search(period or "")
    return int(match.group(1)) if match else None


async def verified_production(session: AsyncSession, *, entity_type: str | None = None,
                              entity_id: uuid.UUID | None = None, year: int | None = None,
                              year_from: int | None = None, year_to: int | None = None,
                              state: str | None = None, commodity: str | None = None,
                              unit: str | None = None, limit: int | None = None,
                              offset: int = 0, record_ids: list[uuid.UUID] | None = None) -> tuple[list[dict], list[dict]]:
    record = TrustedProductionRecord
    query = _apply_filters(_verified_query(), entity_type=entity_type, entity_id=entity_id,
        year=year, year_from=year_from, year_to=year_to, state=state, commodity=commodity, record_ids=record_ids)
    if record_ids is not None: query = query.where(record.id.in_(record_ids))
    rows = (await session.execute(query.order_by(record.reporting_period, record.id)
        .offset(offset).limit(limit) if limit is not None else query.order_by(record.reporting_period, record.id))).all()
    results, issues = [], []
    for prod, mine, project in rows:
        year_value = reporting_year(prod.reporting_period)
        if year is not None and year_value != year: continue
        if year_from is not None and (year_value is None or year_value < year_from): continue
        if year_to is not None and (year_value is None or year_value > year_to): continue
        try:
            normalized = normalize_value(prod.production_value, prod.production_unit)
            if normalized["dimension"] != "mass":
                raise ValueError("Production records must use a mass unit")
            if unit:
                normalized = normalize_value(prod.production_value, prod.production_unit)
                from app.analytics.units import convert
                normalized["normalized_value"] = str(convert(prod.production_value, prod.production_unit, unit))
                normalized["normalized_unit"] = unit
        except (ValueError, ArithmeticError) as exc:
            issues.append({"record_id": str(prod.id), "status": "INVALID_UNIT_OR_VALUE", "detail": str(exc),
                "provenance": production_provenance(prod)})
            continue
        value = Decimal(normalized["normalized_value"])
        if year_value is None:
            issues.append({"record_id": str(prod.id), "status": "MISSING_REPORTING_YEAR",
                "detail": "Reporting period does not contain a supported four-digit year",
                "provenance": production_provenance(prod)})
        if prod.mine_id is None and prod.project_id is None:
            issues.append({"record_id": str(prod.id), "status": "MISSING_ENTITY_REFERENCE",
                "detail": "Verified record has no mine or project reference; entity comparisons are unavailable",
                "provenance": production_provenance(prod)})
        if value < 0:
            issues.append({"record_id": str(prod.id), "status": "NEGATIVE_PRODUCTION", "detail": "Verified production is negative", "provenance": production_provenance(prod)})
            continue
        results.append({"record_id": str(prod.id), "mine_id": str(prod.mine_id) if prod.mine_id else None,
            "mine": mine.canonical_name if mine else None, "state": mine.state if mine else None,
            "company": (mine.operator if mine else None) or (project.organization if project else None),
            "project_id": str(prod.project_id) if prod.project_id else None,
            "project": project.canonical_name if project else None, "period": prod.reporting_period,
            "year": year_value, "commodity": prod.commodity, "original_value": str(prod.production_value),
            "original_unit": prod.production_unit, "value": str(value), "unit": normalized["normalized_unit"],
            "conversion_rule": normalized["conversion_rule"], "dimension": normalized["dimension"],
            "verification_id": str(prod.verification_id), "provenance": production_provenance(prod)})
    return results, issues


async def count_verified_production(session: AsyncSession, **filters) -> int:
    query = _apply_filters(select(func.count(TrustedProductionRecord.id)).select_from(TrustedProductionRecord)
        .join(CanonicalRecord, CanonicalRecord.id == TrustedProductionRecord.verification_id)
        .outerjoin(TrustedProject, TrustedProject.id == TrustedProductionRecord.project_id)
        .outerjoin(TrustedMine, TrustedMine.id == func.coalesce(TrustedProductionRecord.mine_id, TrustedProject.mine_id))
        .where(TrustedProductionRecord.verification_status == "VERIFIED", CanonicalRecord.verified_by.is_not(None),
               CanonicalRecord.verified_by == TrustedProductionRecord.verifier_id), **filters)
    return int(await session.scalar(query) or 0)


def production_provenance(row: TrustedProductionRecord) -> dict:
    return {key: str(getattr(row, key)) if getattr(row, key) is not None else None for key in (
        "id", "source_candidate_id", "verification_id", "verifier_id", "source_document_id",
        "source_version_id", "source_page_id", "source_content_id", "source_table_id", "source_cell_id")}


def group_production(rows: list[dict], group_by: str) -> list[dict]:
    field = {"year": "year", "state": "state", "mine": "mine", "company": "company", "commodity": "commodity"}[group_by]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = row.get(field)
        groups[str(key) if key is not None else "UNSPECIFIED"].append(row)
    items = []
    for key, group in sorted(groups.items()):
        amounts = [Decimal(row["value"]) for row in group]
        items.append({"group_by": group_by, "key": key, "total": str(sum(amounts, Decimal(0))),
            "unit": group[0]["unit"], "record_count": len(group),
            "source_record_ids": [row["record_id"] for row in group],
            "provenance": [row["provenance"] for row in group]})
    return items


def trend_analysis(rows: list[dict]) -> dict:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row["year"] is not None: grouped[row["year"]].append(row)
    observations = [{"year": year, "value": str(sum((Decimal(r["value"]) for r in grouped[year]), Decimal(0))),
        "unit": grouped[year][0]["unit"], "record_count": len(grouped[year]),
        "source_record_ids": [r["record_id"] for r in grouped[year]],
        "provenance": [r["provenance"] for r in grouped[year]]} for year in sorted(grouped)]
    stats = statistics_summary([item["value"] for item in observations], unit=observations[0]["unit"] if observations else None)
    changes = []
    for before, after in zip(observations, observations[1:]):
        prior, current = Decimal(before["value"]), Decimal(after["value"])
        delta = production_difference(current, prior, current_unit=after["unit"], previous_unit=before["unit"])
        rate = growth_percentage(current, prior, current_unit=after["unit"], previous_unit=before["unit"]) if prior else None
        changes.append({"from_year": before["year"], "to_year": after["year"], "change": str(delta.value),
            "change_percent": str(rate.value) if rate else None, "unit": delta.unit,
            "source_record_ids": before["source_record_ids"] + after["source_record_ids"]})
    growth = None
    if len(observations) >= 2:
        first, last = observations[0], observations[-1]
        elapsed = last["year"] - first["year"]
        if elapsed > 0 and Decimal(first["value"]) > 0:
            growth = str(cagr(first["value"], last["value"], elapsed, unit=first["unit"]).value)
    return {"status": "OK" if observations else "INSUFFICIENT_VERIFIED_DATA", "observations": observations,
        "period_changes": changes, "statistics": stats, "cagr_percent": growth,
        "cagr_method": "endpoint CAGR across elapsed calendar years"}


def cross_source_checks(rows: list[dict], issues: list[dict]) -> list[dict]:
    results = [{"validation_type": issue["status"], "severity": "WARNING" if issue["status"].startswith("MISSING_") else "ERROR",
        "status": "REVIEW_REQUIRED" if issue["status"].startswith("MISSING_") else "FAILED",
        "compared_records": [issue["record_id"]], "difference": None, "tolerance": None,
        "provenance": issue["provenance"], "detail": issue["detail"]} for issue in issues]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        entity_id = row["mine_id"] or row["project_id"]
        if entity_id is None:
            continue
        key = (entity_id, row["period"], (row["commodity"] or "").casefold())
        groups[key].append(row)
    for group in groups.values():
        if len(group) < 2: continue
        vals = {Decimal(row["value"]) for row in group}
        same = len(vals) == 1
        results.append({"validation_type": "DUPLICATE_OBSERVATION" if same else "CONFLICTING_PRODUCTION_VALUES",
            "severity": "WARNING" if same else "ERROR", "status": "DUPLICATE" if same else "CONFLICT",
            "compared_records": [row["record_id"] for row in group],
            "difference": str(max(vals) - min(vals)), "tolerance": "0",
            "provenance": [row["provenance"] for row in group],
            "detail": "Same entity, period, and commodity has multiple verified observations"})
    return results


def find_anomalies(rows: list[dict], method: str, threshold: Decimal | float) -> list[dict]:
    source = [{"record_id": row["record_id"], "period": row["period"], "value": row["value"],
        "unit": row["unit"], "provenance": row["provenance"]} for row in rows]
    return detect_anomalies(source, method=method, threshold=threshold)


def calculate_from_rows(kind: str, rows: list[dict], years: int | None = None) -> dict:
    if kind in {"stripping_ratio", "productivity"}:
        raise LookupError(f"INSUFFICIENT_VERIFIED_DATA: {kind} requires trusted fields not present in the current production schema")
    if not rows: raise LookupError("INSUFFICIENT_VERIFIED_DATA")
    if kind in {"production_difference", "growth_percentage", "cagr"}:
        if len(rows) != 2:
            raise ValueError(f"{kind} requires exactly two verified production records")
        rows = sorted(rows, key=lambda row: (row["year"] if row["year"] is not None else -1, row["period"]))
        first, last = rows[0], rows[-1]
        if (first["mine_id"], first["project_id"], first["commodity"]) != (last["mine_id"], last["project_id"], last["commodity"]):
            raise ValueError("Period calculations require the same mine/project and commodity")
        if first["period"] == last["period"]:
            raise ValueError("Period calculations require distinct reporting periods")
    units = {row["unit"] for row in rows}
    if len(units) != 1: raise ValueError("Calculation inputs have incompatible units")
    values = [Decimal(row["value"]) for row in rows]
    refs = tuple(row["record_id"] for row in rows)
    meta = {"values": [str(value) for value in values], "unit": next(iter(units)),
        "normalization": [{"record_id": row["record_id"], "original_value": row["original_value"],
            "original_unit": row["original_unit"], "normalized_value": row["value"],
            "normalized_unit": row["unit"], "conversion_rule": row["conversion_rule"]} for row in rows]}
    if kind in {"production_difference", "growth_percentage", "production_share"}:
        if len(rows) != 2: raise ValueError(f"{kind} requires exactly two verified production records")
        left, right = (values[-1], values[0]) if kind != "production_share" else (values[0], values[1])
        left_unit, right_unit = (rows[-1]["unit"], rows[0]["unit"]) if kind != "production_share" else (rows[0]["unit"], rows[1]["unit"])
        if kind == "production_share":
            result = production_share(left, right, component_unit=left_unit, total_unit=right_unit, source_inputs=refs)
        else:
            fn = production_difference if kind == "production_difference" else growth_percentage
            result = fn(left, right, current_unit=left_unit, previous_unit=right_unit, source_inputs=refs)
    elif kind == "cagr":
        if len(rows) != 2 or years is None: raise ValueError("CAGR requires exactly two records and an explicit positive years value")
        result = cagr(values[0], values[-1], years, unit=rows[0]["unit"], source_inputs=refs)
    else:
        op_name = {"sum": "sum", "average": "mean", "minimum": "min", "maximum": "max",
                   "median": "median", "standard_deviation": "population_standard_deviation"}[kind]
        if kind == "sum": value = sum(values, Decimal(0))
        elif kind == "average": value = sum(values, Decimal(0)) / len(values)
        elif kind == "minimum": value = min(values)
        elif kind == "maximum": value = max(values)
        elif kind == "median":
            value = Decimal(statistics_summary(values)["median"])
        else: value = Decimal(statistics_summary(values)["standard_deviation"])
        from app.analytics.deterministic import CalculationResult
        result = CalculationResult(kind, value, rows[0]["unit"], refs,
            {"formula_id": op_name, "formula_version": "1.0", "method": "population" if kind == "standard_deviation" else "exact deterministic"})
    return {"calculation_type": result.operation, "value": str(result.value), "unit": result.unit,
        "formula_id": result.metadata["formula_id"], "formula_version": result.metadata["formula_version"],
        "input_snapshot": {"records": rows}, "normalized_inputs": meta,
        "source_record_ids": list(refs), "provenance": [row["provenance"] for row in rows], "metadata": result.metadata}
