from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.extraction import ExtractionCandidate
from app.models.intelligence import ExtractedContent, ExtractedTable, ExtractedTableCell

logger = logging.getLogger(__name__)
_NS = uuid.UUID("03ae188d-b4a5-4344-a3bb-829b0ad16d8e")


@dataclass(frozen=True)
class Unit:
    canonical: str
    dimension: str
    to_base: Decimal


_UNIT_ALIASES: dict[str, Unit] = {}


def _unit(canonical: str, dimension: str, factor: str, *aliases: str) -> None:
    item = Unit(canonical, dimension, Decimal(factor))
    for alias in (canonical, *aliases):
        _UNIT_ALIASES[alias.casefold().strip().replace("²", "2")] = item


_unit("t", "mass", "1", "tonne", "tonnes", "metric tonne", "metric tonnes")
_unit("kt", "mass", "1000", "kilotonne", "kilotonnes", "kt")
_unit("Mt", "mass", "1000000", "million tonne", "million tonnes", "million tons")
_unit("m", "length", "1", "metre", "metres", "meter", "meters")
_unit("km", "length", "1000", "kilometre", "kilometres", "kilometer", "kilometers")
_unit("ha", "area", "10000", "hectare", "hectares")
_unit("m2", "area", "1", "square metre", "square metres", "square meter", "square meters", "sqm", "m2")
_unit("%", "percentage", "1", "percent", "percentage", "%")
_unit("degree", "angle", "1", "degrees", "deg", "°")


def normalize_unit(raw_unit: str | None) -> Unit | None:
    if raw_unit is None:
        return None
    if raw_unit.strip() == "Mt":
        return Unit("Mt", "mass", Decimal("1000000"))
    if raw_unit.strip().casefold() == "mt":
        return None
    if raw_unit.strip().casefold() == "mtpa":
        return None
    key = raw_unit.casefold().strip().replace("²", "2")
    return _UNIT_ALIASES.get(key)


def convert_value(value: Decimal | int | float | str, from_unit: str, to_unit: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Value is not a valid decimal") from exc
    source, target = normalize_unit(from_unit), normalize_unit(to_unit)
    if not number.is_finite() or source is None or target is None or source.dimension != target.dimension:
        raise ValueError("Value or units are invalid or incompatible")
    return number * source.to_base / target.to_base


_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_VALUE_UNIT_RE = re.compile(rf"(?<![\w.])(?P<value>{_NUMBER})\s*(?P<unit>million\s+tonnes?|kilotonnes?|tonnes?|tons?|Mtpa|Mt|kt|ha|hectares?|km|m2|sqm|square\s+met(?:re|er)s?|met(?:re|er)s?|%|percent(?:age)?|degrees?|deg|°|[A-Za-z][A-Za-z0-9/%².-]*)?", re.I)
_ENTITY_RULES = (
    ("mine", re.compile(r"\b(?:mine|colliery)\s*(?:name\s*)?[:\-]?\s*([A-Z][\w&'()./-]*(?:\s+[A-Z][\w&'()./-]*){0,5})", re.I)),
    ("project", re.compile(r"\bproject\s*(?:name\s*)?[:\-]?\s*([A-Z][\w&'()./-]*(?:\s+[A-Z][\w&'()./-]*){0,5})", re.I)),
    ("coal_block", re.compile(r"\b(?:coal\s+block|block)\s*(?:name\s*)?[:\-]?\s*([A-Z][\w&'()./-]*(?:\s+[A-Z][\w&'()./-]*){0,5})", re.I)),
    ("borehole", re.compile(r"\b(?:borehole|bore\s*hole|BH)\s*(?:no\.?|id|identifier)?\s*[:#\-]?\s*([A-Z0-9][A-Z0-9_./-]{0,30})", re.I)),
    ("exploration_location", re.compile(r"\b(?:exploration\s+location|location)\s*[:\-]\s*([^,;\n]{2,100})", re.I)),
    ("geological_formation", re.compile(r"\b(?:formation|group|member)\s*[:\-]?\s*([A-Z][\w -]{1,50})", re.I)),
    ("seam", re.compile(r"\bseam\s*(?:name|no\.?|identifier)?\s*[:#\-]?\s*([A-Z0-9][\w./-]{0,30})", re.I)),
    ("mineral_resource", re.compile(r"\b(?:mineral|resource|reserve)\s*(?:type|name)?\s*[:\-]?\s*([A-Z][\w -]{1,50})", re.I)),
)
_COORD_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_COORD_PAIR = re.compile(rf"(?<![\w/])(?P<a>{_COORD_NUMBER})\s*[,;/]\s*(?P<b>{_COORD_NUMBER})(?![\w/])")
_DATE_RE = re.compile(r"\b(?P<date>(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/ ](?:\d{1,2}|[A-Za-z]{3,9})[-/ ,]\d{2,4}))\b")
_TABLE_YEAR = re.compile(r"(?<!\d)(?:18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)(?!\d)")
_TABLE_PERIOD = re.compile(r"(?<!\d)(?:18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)(?:[-/](?:\d{2}|18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200))?(?!\d)")
_MEASUREMENT_LABEL = re.compile(r"\b(?:production|output|dispatch|achievement|reserve|resource|depth|thickness|elevation|area|distance|grade|quality|calorific\s+value|ash|moisture|sulphur|sulfur|latitude|longitude|coordinate|tonnage|quantity|target|overburden)\b", re.I)
_NON_UNIT_WORDS = {"and", "or", "is", "are", "was", "were", "of", "in", "on", "at", "to", "from", "for", "with", "by", "as", "the", "a", "an", "per", "than", "more", "less"}
_DMS_RE = re.compile(r"(?P<deg>\d{1,3})\s*°\s*(?P<min>\d{1,2})\s*[′']\s*(?P<sec>\d{1,2}(?:\.\d+)?)?\s*[″\"]?\s*(?P<hem>[NSEW])?", re.I)
_DM_RE = re.compile(r"(?P<deg>\d{1,3})\s*°\s*(?P<min>\d{1,2}(?:\.\d+)?)\s*[′']\s*(?P<hem>[NSEW])?", re.I)


def parse_coordinate_pair(raw: str) -> tuple[Decimal, Decimal] | None:
    """Return (longitude, latitude), accepting decimal, DM, and DMS pairs."""
    dms = list(_DMS_RE.finditer(raw))
    if len(dms) >= 2:
        vals = []
        for match in dms[:2]:
            degree = Decimal(match["deg"])
            minute = Decimal(match["min"])
            second = Decimal(match["sec"] or "0")
            if minute >= 60 or second >= 60:
                return None
            val = degree + minute / 60 + second / 3600
            if (match["hem"] or "").upper() in {"S", "W"}:
                val = -val
            vals.append(val)
        first, second = vals
        # Hemispheres specify the order; absent labels default to longitude, latitude.
        hemis = [(m["hem"] or "").upper() for m in dms[:2]]
        if hemis[0] in {"N", "S"} and hemis[1] in {"E", "W"}:
            first, second = second, first
        return (first, second) if abs(first) <= 180 and abs(second) <= 90 else None
    dm = list(_DM_RE.finditer(raw))
    if len(dm) >= 2:
        vals = []
        for item in dm[:2]:
            minute = Decimal(item["min"])
            if minute >= 60:
                return None
            value = Decimal(item["deg"]) + minute / 60
            if (item["hem"] or "").upper() in {"S", "W"}:
                value = -value
            vals.append(value)
        hemis = [(item["hem"] or "").upper() for item in dm[:2]]
        first, second = vals
        if hemis[0] in {"N", "S"} and hemis[1] in {"E", "W"}:
            first, second = second, first
        return (first, second) if abs(first) <= 180 and abs(second) <= 90 else None
    match = _COORD_PAIR.search(raw)
    if not match:
        return None
    first, second = Decimal(match["a"].replace(",", "")), Decimal(match["b"].replace(",", ""))
    # A coordinate pair without labels is interpreted as latitude, longitude only
    # when the first value cannot be a longitude; otherwise order remains unresolved.
    if abs(first) <= 90 and abs(second) <= 180 and (abs(first) > 90 or abs(second) > 90):
        return second, first
    if abs(first) <= 180 and abs(second) <= 90:
        return first, second
    return None


def coordinate_validation(lon: Decimal, lat: Decimal) -> list[dict[str, str]]:
    results = []
    if not lon.is_finite() or not lat.is_finite() or abs(lon) > 180 or abs(lat) > 90:
        results.append({"rule": "coordinate_range", "status": "FAILED", "message": "Longitude must be within ±180 and latitude within ±90 degrees"})
    else:
        results.append({"rule": "coordinate_range", "status": "PASSED", "message": "Coordinate is within WGS84 numeric ranges"})
    return results


def coordinate_point_ewkt(lon: Decimal, lat: Decimal) -> str:
    if any(item["status"] == "FAILED" for item in coordinate_validation(lon, lat)):
        raise ValueError("Invalid coordinates cannot be converted to a PostGIS point")
    return f"SRID=4326;POINT({lon} {lat})"


def reconstruct_table_rows(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[int, dict[str, Any]]] = {}
    for cell in cells:
        grouped.setdefault(int(cell["row_index"]), {})[int(cell["column_index"])] = cell
    if not grouped:
        return []
    indexes = sorted(grouped)
    header_index = indexes[0]
    headers = {col: (item.get("raw_value") or "").strip() for col, item in grouped[header_index].items()}
    rows = []
    for row_index in indexes[1:]:
        values = grouped[row_index]
        rows.append({"row_index": row_index, "cells": values,
            "headers_by_column": headers,
            "values_by_header": {headers.get(col) or str(col): item.get("raw_value") for col, item in values.items()}})
    return rows


def extract_spatial_table_rows(rows: list[dict[str, Any]], table_id: uuid.UUID) -> list[dict[str, Any]]:
    """Build reviewable spatial candidates from explicitly labeled table rows.

    Latitude and longitude are paired only when they occur under corresponding
    table headers in the same row. The generated candidates remain PENDING.
    """
    candidates = []
    for row in rows:
        values = {re.sub(r"[^a-z0-9]+", "", str(key).casefold()): value
                  for key, value in row["values_by_header"].items()}
        entity_type = str(values.get("entitytype") or values.get("recordtype") or values.get("type") or "").strip().upper()
        name = str(values.get("name") or values.get("entityname") or values.get("boreholename") or "").strip()
        latitude_raw = values.get("latitude", values.get("lat"))
        longitude_raw = values.get("longitude", values.get("lon", values.get("long")))
        if entity_type not in {"BOREHOLE", "VERIFIED_COORDINATE", "COORDINATE", "MINE"} or not name:
            continue

        metadata = {
            "rule": "labeled_spatial_table_row", "table_row_index": row["row_index"],
            "display_name": name, "spatial_entity_type": entity_type,
            "source_table_id": str(table_id),
            "source_cells": {str(row.get("headers_by_column", {}).get(col, col)): str(cell["id"])
                for col, cell in row["cells"].items()},
        }
        for header, value in row["values_by_header"].items():
            key = re.sub(r"[^a-z0-9]+", "", str(header).casefold())
            if key in {"state", "district", "mine", "description", "elevationm", "elevation", "depthm", "depth", "coordinatesystem", "crs"} and value is not None:
                metadata[{
                    "elevationm": "elevation", "elevation": "elevation", "depthm": "depth",
                    "coordinatesystem": "coordinate_system", "crs": "coordinate_system",
                }.get(key, key)] = value

        validations: list[dict[str, str]] = []
        if latitude_raw is not None and longitude_raw is not None:
            try:
                latitude, longitude = Decimal(str(latitude_raw).strip()), Decimal(str(longitude_raw).strip())
                validations = coordinate_validation(longitude, latitude)
                metadata.update({"latitude": str(latitude), "longitude": str(longitude)})
                if not metadata.get("coordinate_system"):
                    # Explicit latitude/longitude headers define geographic degrees.
                    metadata["coordinate_system"] = "EPSG:4326"
            except InvalidOperation:
                validations = [{"rule": "coordinate_range", "status": "FAILED", "message": "Latitude and longitude must be numeric"}]
        else:
            validations = [{"rule": "coordinate_range", "status": "WARNING", "message": "Spatial entity row has no complete latitude/longitude pair"}]

        if entity_type in {"VERIFIED_COORDINATE", "COORDINATE"}:
            if latitude_raw is None or longitude_raw is None or any(v["status"] == "FAILED" for v in validations):
                # Keep invalid coordinate evidence reviewable, but make it impossible
                # to approve through the existing validation gate.
                if not any(v["status"] == "FAILED" for v in validations):
                    validations.append({"rule": "coordinate_range", "status": "FAILED", "message": "Coordinate row requires valid latitude and longitude"})
                pair_text = f"{longitude_raw or ''},{latitude_raw or ''}"
            else:
                pair_text = f"{metadata['longitude']},{metadata['latitude']}"
            # Use the name cell as the primary reference; all row cell IDs,
            # including the separate latitude and longitude cells, remain in metadata.
            name_column = next((col for col, header in row.get("headers_by_column", {}).items()
                if re.sub(r"[^a-z0-9]+", "", str(header).casefold()) in {"name", "entityname", "boreholename"}), None)
            name_cell = row["cells"].get(name_column)
            candidates.append({"candidate_type": "coordinate", "raw_value": pair_text,
                "normalized_value": pair_text,
                "raw_text": f"{name}; latitude {latitude_raw}; longitude {longitude_raw}",
                "metadata": metadata, "validation": validations,
                "source_cell_id": (name_cell or {}).get("id"),
                "row_index": row["row_index"]})
        else:
            candidates.append({"candidate_type": "borehole" if entity_type == "BOREHOLE" else "mine",
                "raw_value": name, "normalized_value": name,
                "raw_text": f"{entity_type}: {name}; latitude {latitude_raw}; longitude {longitude_raw}",
                "metadata": metadata, "validation": validations,
                "source_cell_id": next((cell.get("id") for col, cell in row["cells"].items()
                    if str(cell.get("raw_value") or "").strip() == name), None),
                "row_index": row["row_index"]})
    return candidates


def extract_production_table_rows(
    rows: list[dict[str, Any]],
    table_id: uuid.UUID,
    *,
    mass_unit_definitions: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract source-backed production amounts from labeled, possibly multi-row tables.

    Column headers can span several extracted rows, as in the Coal Directory tables.
    Ambiguous ``MT`` is accepted only when the same source document contains its
    explicit abbreviation definition; otherwise normal unit validation rejects it.
    """
    if not rows:
        return []

    amount_re = re.compile(rf"^\s*(?P<value>{_NUMBER})\s*(?P<unit>[A-Za-z%²]+)?\s*$")
    period_headers = {"period", "reportingperiod", "reportingyear", "financialyear", "year"}
    excluded_measure = re.compile(r"%|percent|share|growth|change|rate|average|difference|index", re.I)
    explicit_measure = re.compile(r"production|output|dispatch|achievement|quantity|tonnage", re.I)
    unit_re = re.compile(r"\b(million\s+tonnes?|kilotonnes?|tonnes?|tons?|Mt|MT|kt)\b", re.I)

    header_columns = rows[0].get("headers_by_column", {})
    table_title = " ".join(str(value).strip() for value in header_columns.values() if value).strip()
    if not explicit_measure.search(table_title):
        return []
    title_period_match = _TABLE_PERIOD.search(table_title)
    title_period = title_period_match.group(0) if title_period_match else None
    definition_map = mass_unit_definitions or {}

    raw_unit = None
    unit_match = unit_re.search(table_title)
    if unit_match:
        raw_unit = unit_match.group(1)
    if raw_unit is None:
        raw_unit = next((str(value).strip() for row in rows for value in row.get("values_by_header", {}).values()
            if value and (unit_match := unit_re.search(str(value)))), None)
    definition = definition_map.get(raw_unit or "")
    normalized_unit = normalize_unit("Mt" if raw_unit and raw_unit.casefold() == "mt" and definition else raw_unit)
    if normalized_unit is None or normalized_unit.dimension != "mass":
        return []

    # Determine where numeric data starts. Until then, combine stacked headings
    # such as Year / Coking Coal / Quantity into a usable column label.
    data_start = None
    for index, row in enumerate(rows):
        first_cell = row.get("cells", {}).get(0) or row.get("cells", {}).get("0")
        row_label = str(first_cell.get("raw_value") or "").strip() if first_cell else ""
        row_label_key = re.sub(r"[^a-z]+", "", row_label.casefold())
        if not row_label or row_label_key in {"year", "years", "period", "reportingperiod", "reportingyear",
                "financialyear", "country", "countries", "countrygroup", "state", "states", "sector",
                "company", "commodity", "mineral", "production", "unit", "quantity"}:
            continue
        if any(amount_re.fullmatch(str(cell.get("raw_value") or "")) for col, cell in row["cells"].items()
               if str(col) != "0"):
            data_start = index
            break
    if data_start is None:
        return []

    column_labels: dict[str, list[str]] = {}
    for col, header in header_columns.items():
        if header:
            column_labels.setdefault(str(col), []).append(str(header).strip())
    for row in rows[:data_start]:
        for col, cell in row["cells"].items():
            value = str(cell.get("raw_value") or "").strip()
            if value and not re.fullmatch(r"\(\s*\d+\s*\)", value):
                column_labels.setdefault(str(col), []).append(value)
    labels = {col: " ".join(dict.fromkeys(parts)) for col, parts in column_labels.items()}

    output = []
    for row in rows[data_start:]:
        first_cell = row.get("cells", {}).get(0) or row.get("cells", {}).get("0")
        row_label = str(first_cell.get("raw_value") or "").strip() if first_cell else ""
        normalized_headers = {re.sub(r"[^a-z0-9]+", "", str(header).casefold()): value
            for header, value in row.get("values_by_header", {}).items()}
        period_value = next((value for key, value in normalized_headers.items()
            if key in period_headers and value is not None and _TABLE_YEAR.search(str(value))), None)
        if period_value is None and _TABLE_YEAR.fullmatch(row_label):
            period_value = row_label

        for column, cell in row["cells"].items():
            column_key = str(column)
            if column_key == "0":
                continue
            column_label = labels.get(column_key, "")
            if excluded_measure.search(column_label):
                continue
            match = amount_re.fullmatch(str(cell.get("raw_value") or ""))
            if not match:
                continue
            header_period_match = _TABLE_PERIOD.search(column_label)
            period = str(period_value or (header_period_match.group(0) if header_period_match else title_period or "")).strip()
            if not _TABLE_YEAR.search(period):
                continue

            cell_unit = match.group("unit")
            effective_unit = normalize_unit("Mt" if cell_unit and cell_unit.casefold() == "mt" and definition_map.get(cell_unit) else cell_unit) if cell_unit else normalized_unit
            if effective_unit is None or effective_unit.dimension != "mass":
                continue
            try:
                number = Decimal(match.group("value").replace(",", ""))
            except InvalidOperation:
                continue
            if not number.is_finite():
                continue
            normalized_value = number * effective_unit.to_base
            readable_parts = [part for part in column_labels.get(column_key, [])
                if not (explicit_measure.search(part) and (len(part) > 32 or _TABLE_PERIOD.search(part)))]
            label_for_record = " ".join(readable_parts) or column_label
            label_for_record = re.sub(r"\b(?:million\s+tonnes?|kilotonnes?|tonnes?|tons?|Mt|MT|kt)\b", "", label_for_record, flags=re.I)
            label_for_record = re.sub(r"\s+", " ", label_for_record).strip(" -:|\n") or "production"
            commodity = label_for_record
            if row_label and row_label.casefold() not in {"india", "all india", "world", "total"}:
                commodity = f"{row_label} — {label_for_record}"
            metadata = {"rule": "production_table_row", "reporting_period": period,
                "commodity": commodity, "source_unit": cell_unit or raw_unit,
                "production_source_header": label_for_record, "source_table_title": table_title,
                "source_row_label": row_label, "table_row_index": row["row_index"],
                "source_table_id": str(table_id),
                "source_cells": {str(labels.get(str(col), col)): str(source_cell["id"])
                    for col, source_cell in row["cells"].items()},
                "unit_definition": definition.get("definition") if definition else None,
                "unit_definition_page_id": definition.get("source_page_id") if definition else None}
            output.append({"candidate_type": "production_record",
                "raw_text": f"{row_label}: {label_for_record} = {cell.get('raw_value')} {cell_unit or raw_unit}",
                "raw_value": match.group("value"), "normalized_value": str(normalized_value),
                "normalized_numeric_value": normalized_value, "unit": "t", "metadata": metadata,
                "validation": [{"rule": "production_nonnegative", "status": "FAILED" if number < 0 else "PASSED",
                    "message": "Production values cannot be negative" if number < 0 else "Production amount and unit are valid"}],
                "source_cell_id": str(cell["id"]), "row_index": row["row_index"]})
    return output


def entity_match_status(value: str, prior_values: list[str]) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", s).casefold())
    key = norm(value)
    if any(norm(prior) == key for prior in prior_values):
        return "MATCHED"
    if any(key.startswith(norm(prior)) or norm(prior).startswith(key) for prior in prior_values if norm(prior)):
        return "POSSIBLE_MATCH"
    return "UNRESOLVED"


def extract_candidates(raw_text: str) -> list[dict[str, Any]]:
    """Apply generic label/value patterns; retain unresolved numbers without unit guesses."""
    candidates: list[dict[str, Any]] = []
    for kind, pattern in _ENTITY_RULES:
        for match in pattern.finditer(raw_text):
            value = match.group(1).strip(" .,:;-")
            candidates.append({"candidate_type": kind, "raw_text": match.group(0), "raw_value": value, "normalized_value": value, "metadata": {"start": match.start(), "end": match.end(), "rule": kind}})
    date_spans = []
    for match in _DATE_RE.finditer(raw_text):
        date_spans.append(match.span())
        raw = match["date"]
        normalized = None
        try:
            normalized = date.fromisoformat(raw.replace("/", "-")).isoformat()
        except ValueError:
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%d-%m-%y"):
                try:
                    from datetime import datetime
                    normalized = datetime.strptime(raw, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
        candidates.append({"candidate_type": "date_time", "raw_text": raw, "raw_value": raw, "normalized_value": normalized, "metadata": {"start": match.start(), "end": match.end(), "rule": "date_pattern"}, "validation": [{"rule": "date_valid", "status": "PASSED" if normalized else "FAILED", "message": "Date parsed" if normalized else "Invalid or unsupported date"}]})
    explicit_unit_spans = [match.span() for match in _VALUE_UNIT_RE.finditer(raw_text) if match["unit"]]
    coordinate_matches = [match for match in _COORD_PAIR.finditer(raw_text) if not any(match.start() < end and match.end() > start for start, end in date_spans + explicit_unit_spans)]
    coordinate_spans = [match.span() for match in coordinate_matches]
    for match in _VALUE_UNIT_RE.finditer(raw_text):
        if any(match.start() < end and match.end() > start for start, end in (*date_spans, *coordinate_spans)):
            continue
        raw_value = match["value"]
        unit_raw = match["unit"]
        unit = normalize_unit(unit_raw)
        prefix = raw_text[max(0, match.start() - 70):match.start()]
        labelled = bool(_MEASUREMENT_LABEL.search(prefix))
        if unit_raw and unit is None and (unit_raw.casefold().strip() in _NON_UNIT_WORDS or not labelled):
            continue
        if not unit_raw and not labelled:
            continue
        raw = match.group(0).strip()
        try:
            value = Decimal(raw_value.replace(",", ""))
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        base_unit = {"mass": "t", "length": "m", "area": "m2", "percentage": "%", "angle": "degree"}.get(unit.dimension) if unit else None
        candidate_type = "production_record" if re.search(r"(?:production|output|dispatch|achievement)\D*$", prefix, re.I) else "measurement"
        rules = []
        if value < 0 and re.search(r"(?:production|output|dispatch|depth|thickness|area|distance|reserve|resource)\D*$", prefix, re.I):
            rules.append({"rule": "nonnegative_domain_value", "status": "FAILED", "message": "Negative value is not valid for this labeled quantity"})
        candidates.append({"candidate_type": candidate_type, "raw_text": raw, "raw_value": raw_value, "numeric": value * unit.to_base if unit else None, "unit": base_unit, "normalized_value": str(value * unit.to_base) if unit else None, "validation": rules, "metadata": {"start": match.start(), "end": match.end(), "rule": "number_unit_pattern", "dimension": unit.dimension if unit else None, "source_unit": unit_raw, "unit_unresolved": unit is None}})
    for match in coordinate_matches:
        pair = parse_coordinate_pair(match.group(0))
        candidates.append({"candidate_type": "coordinate", "raw_text": match.group(0), "raw_value": match.group(0), "normalized_value": f"{pair[0]},{pair[1]}" if pair else None, "numeric": None, "metadata": {"start": match.start(), "end": match.end(), "rule": "coordinate_pair", "longitude": str(pair[0]) if pair else None, "latitude": str(pair[1]) if pair else None}, "validation": coordinate_validation(*pair) if pair else [{"rule": "coordinate_range", "status": "FAILED", "message": "Coordinate order or range is unresolved"}]})
    dms = list(_DMS_RE.finditer(raw_text))
    if len(dms) >= 2:
        raw_coord = raw_text[dms[0].start():dms[1].end()]
        pair = parse_coordinate_pair(raw_coord)
        candidates.append({"candidate_type": "coordinate", "raw_text": raw_coord, "raw_value": raw_coord, "normalized_value": f"{pair[0]},{pair[1]}" if pair else None, "numeric": None, "metadata": {"start": dms[0].start(), "end": dms[1].end(), "rule": "coordinate_dms", "longitude": str(pair[0]) if pair else None, "latitude": str(pair[1]) if pair else None}, "validation": coordinate_validation(*pair) if pair else [{"rule": "coordinate_range", "status": "FAILED", "message": "Coordinate order or range is unresolved"}]})
    # Content is source evidence; duplicate matches from overlapping generic patterns are removed deterministically.
    seen = set()
    result = []
    for candidate in sorted(candidates, key=lambda c: (c["metadata"]["start"], c["candidate_type"])):
        key = (candidate["candidate_type"], candidate["metadata"]["start"], candidate["metadata"]["end"])
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


async def extract_document_version(session: AsyncSession, document_id: uuid.UUID) -> dict[str, Any]:
    started = time.monotonic()
    document = await session.get(Document, document_id)
    if document is None:
        raise LookupError("Document not found")
    result = await session.execute(select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_number.desc()).limit(1))
    version = result.scalar_one_or_none()
    if version is None:
        raise LookupError("Document version not found")
    pages_result = await session.execute(select(DocumentPage).where(DocumentPage.document_version_id == version.id).order_by(DocumentPage.page_number.nullslast(), DocumentPage.created_at))
    pages = list(pages_result.scalars().all())
    logger.info("Structured extraction started document_id=%s version_id=%s pages=%d", document_id, version.id, len(pages))
    unit_definitions = await _document_mass_unit_definitions(session, version.id)
    # Re-extraction must never delete verified candidates or their provenance.
    # Deterministic IDs make repeated extraction append-only and idempotent.
    existing_ids = set((await session.scalars(select(ExtractionCandidate.id).where(
        ExtractionCandidate.document_version_id == version.id))).all())
    persisted = []
    unresolved = failures = 0
    for page in pages:
        source_items: list[tuple[str | None, str | None, str | None, str, Decimal | None, int | None, int | None, str | None]] = []
        content_result = await session.execute(select(ExtractedContent).where(ExtractedContent.document_page_id == page.id).order_by(ExtractedContent.created_at))
        page_contents = list(content_result.scalars().all())
        for content in page_contents:
            source_items.append((str(content.id), None, None, content.text, content.confidence, None, None, None))
        tables_result = await session.execute(select(ExtractedTable).where(ExtractedTable.document_page_id == page.id).order_by(ExtractedTable.table_order))
        page_tables = list(tables_result.scalars().all())
        for table in page_tables:
            cells_result = await session.execute(select(ExtractedTableCell).where(ExtractedTableCell.table_id == table.id).order_by(ExtractedTableCell.row_index, ExtractedTableCell.column_index))
            cells = list(cells_result.scalars().all())
            # Cells remain independent provenance units; table rows are reconstructed for reusable consumers.
            rows = reconstruct_table_rows([{"id": str(c.id), "row_index": c.row_index, "column_index": c.column_index, "raw_value": c.raw_value} for c in cells])
            row_candidates = extract_spatial_table_rows(rows, table.id) + extract_production_table_rows(
                rows, table.id, mass_unit_definitions=unit_definitions)
            row_source_cells = {item["source_cell_id"] for item in row_candidates if item.get("source_cell_id")}
            for item in row_candidates:
                source_cell = item.get("source_cell_id")
                row_reference = f"row:{item['row_index']}:{source_cell}" if item["candidate_type"] == "production_record" else f"row:{item['row_index']}"
                row_key = "|".join((str(version.id), str(table.id), row_reference,
                    item["candidate_type"], "table_spatial_row"))
                candidate_id = uuid.uuid5(_NS, row_key)
                if candidate_id in existing_ids:
                    continue
                metadata = {**item["metadata"], "row_index": item["row_index"],
                    "column_index": None, "extraction_source": "table_row"}
                validations = item.get("validation", [])
                failures += sum(value["status"] == "FAILED" for value in validations)
                unresolved += int(item.get("normalized_value") is None)
                persisted.append(ExtractionCandidate(
                    id=candidate_id, document_id=document.id, document_version_id=version.id,
                    source_page_id=page.id, source_table_id=table.id,
                    source_cell_id=uuid.UUID(item["source_cell_id"]) if item.get("source_cell_id") else None,
                    candidate_type=item["candidate_type"], raw_text=item["raw_text"],
                    raw_value=item.get("raw_value"), normalized_value=item.get("normalized_value"),
                    normalized_numeric_value=item.get("normalized_numeric_value"), unit=item.get("unit"),
                    verification_status="PENDING", match_status="UNRESOLVED",
                    validation_results=validations, candidate_metadata=metadata,
                ))
                existing_ids.add(candidate_id)
            first_row = min((c.row_index for c in cells), default=0)
            headers = {c.column_index: (c.raw_value or "").strip() for c in cells if c.row_index == first_row}
            for cell in cells:
                if str(cell.id) in row_source_cells:
                    continue
                header = headers.get(cell.column_index) if cell.row_index != first_row else None
                context = f"{header}: {cell.raw_value}" if header and re.search(r"mine|project|block|borehole|seam|formation|mineral|reserve|production|output|depth|thickness|elevation|coordinate|latitude|longitude|date", header, re.I) else None
                source_items.append((None, str(table.id), str(cell.id), cell.raw_value or "", cell.confidence, cell.row_index, cell.column_index, context))
            logger.info("Structured extraction table reconstructed table_id=%s rows=%d", table.id, len(rows))
        if page.text and not page_contents and not page_tables:
            source_items.append((None, None, None, page.text, None, None, None, None))
        for content_id, table_id, cell_id, text_value, confidence, row, col, context in source_items:
            for item in extract_candidates(context or text_value):
                if context:
                    item["metadata"]["table_header"] = context.split(":", 1)[0]
                    if item.get("raw_value") is not None:
                        item["raw_text"] = text_value
                source_key = content_id or cell_id or str(page.id)
                candidate_id = uuid.uuid5(_NS, "|".join((str(version.id), source_key, item["candidate_type"], str(item["metadata"]["start"]), str(item["metadata"]["end"]))))
                if candidate_id in existing_ids:
                    continue
                validations = item.get("validation", [])
                failures += sum(v["status"] == "FAILED" for v in validations)
                unresolved += int(item.get("normalized_value") is None)
                match_status = "UNRESOLVED"
                if item["candidate_type"] in {"mine", "project", "coal_block", "borehole", "exploration_location", "geological_formation", "seam", "mineral_resource"}:
                    prior_values = [row.raw_value or "" for row in persisted if row.candidate_type == item["candidate_type"]]
                    match_status = entity_match_status(item.get("raw_value") or "", prior_values)
                persisted.append(ExtractionCandidate(
                    id=candidate_id, document_id=document.id, document_version_id=version.id,
                    source_page_id=page.id, source_content_id=uuid.UUID(content_id) if content_id else None,
                    source_table_id=uuid.UUID(table_id) if table_id else None, source_cell_id=uuid.UUID(cell_id) if cell_id else None,
                    candidate_type=item["candidate_type"], raw_text=item["raw_text"], raw_value=item.get("raw_value"),
                    normalized_value=item.get("normalized_value"), normalized_numeric_value=item.get("numeric"), unit=item.get("unit"),
                    extraction_confidence=confidence, verification_status="PENDING", match_status=match_status,
                    validation_results=validations, candidate_metadata={**item["metadata"], "row_index": row, "column_index": col},
                ))
                existing_ids.add(candidate_id)
    session.add_all(persisted)
    await session.commit()
    duration = round(time.monotonic() - started, 4)
    counts: dict[str, int] = {}
    for item in persisted:
        counts[item.candidate_type] = counts.get(item.candidate_type, 0) + 1
    logger.info("Structured extraction complete document_id=%s version_id=%s candidates=%d types=%s unresolved=%d validation_failures=%d duration_seconds=%s", document_id, version.id, len(persisted), counts, unresolved, failures, duration)
    return {"document_id": document.id, "document_version_id": version.id, "candidate_count": len(persisted), "candidate_counts": counts, "unresolved_count": unresolved, "validation_failure_count": failures, "duration_seconds": duration}


async def _document_mass_unit_definitions(session: AsyncSession, version_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """Use abbreviation meanings only when the source document defines them."""
    pages = (await session.execute(select(DocumentPage.id, DocumentPage.page_number, DocumentPage.text)
        .where(DocumentPage.document_version_id == version_id))).all()
    text_by_page = {page_id: [page_text or ""] for page_id, _number, page_text in pages}
    number_by_page = {page_id: number for page_id, number, _text in pages}
    content_rows = (await session.execute(select(ExtractedContent.document_page_id, ExtractedContent.text)
        .join(DocumentPage, DocumentPage.id == ExtractedContent.document_page_id)
        .where(DocumentPage.document_version_id == version_id)
        .order_by(ExtractedContent.created_at))).all()
    for page_id, value in content_rows:
        text_by_page.setdefault(page_id, []).append(value or "")
    cell_rows = (await session.execute(select(ExtractedTable.document_page_id, ExtractedTableCell.raw_value)
        .join(ExtractedTableCell, ExtractedTableCell.table_id == ExtractedTable.id)
        .join(DocumentPage, DocumentPage.id == ExtractedTable.document_page_id)
        .where(DocumentPage.document_version_id == version_id)
        .order_by(ExtractedTable.table_order, ExtractedTableCell.row_index, ExtractedTableCell.column_index))).all()
    for page_id, value in cell_rows:
        text_by_page.setdefault(page_id, []).append(value or "")

    definition_pattern = re.compile(r"\bMT\b[\s:;=–—-]{1,32}Million\s+Tonnes\b", re.I)
    for page_id, fragments in text_by_page.items():
        if definition_pattern.search("\n".join(fragments)):
            return {"MT": {"canonical": "Mt", "definition": "MT = Million Tonnes",
                "source_page_id": str(page_id), "source_page_number": number_by_page.get(page_id)}}
    return {}
