from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.services.structured_extraction import convert_value
from app.analytics.units import convert, normalize_value, unit_dimension


@dataclass(frozen=True)
class CalculationResult:
    operation: str
    value: Decimal | None
    unit: str | None
    source_inputs: tuple[object, ...]
    metadata: dict


def _values(values: Iterable[Decimal | int | float | str | None]) -> list[Decimal]:
    result = []
    for value in values:
        if value is None:
            continue
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Calculation input is not numeric") from exc
        if not number.is_finite():
            raise ValueError("Calculation inputs must be finite")
        result.append(number)
    return result


def total(values: Iterable[Decimal | int | float | str | None], *, unit: str | None = None, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    items = _values(values)
    return CalculationResult("total", sum(items, Decimal(0)) if items else None, unit, source_inputs, {"included_count": len(items), "ignored_null_count": "nulls ignored"})


def average(values: Iterable[Decimal | int | float | str | None], *, unit: str | None = None, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    items = _values(values)
    return CalculationResult("average", sum(items, Decimal(0)) / len(items) if items else None, unit, source_inputs, {"included_count": len(items), "ignored_null_count": "nulls ignored"})


def percentage(part: Decimal | int | float | str | None, whole: Decimal | int | float | str | None, *, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    vals = _values((part, whole))
    if len(vals) != 2:
        return CalculationResult("percentage", None, "%", source_inputs, {"status": "missing_input"})
    if vals[1] == 0:
        raise ValueError("Percentage denominator cannot be zero")
    return CalculationResult("percentage", vals[0] / vals[1] * 100, "%", source_inputs, {"status": "calculated"})


def difference(left: Decimal | int | float | str | None, right: Decimal | int | float | str | None, *, unit: str | None = None, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    vals = _values((left, right))
    return CalculationResult("difference", vals[0] - vals[1] if len(vals) == 2 else None, unit, source_inputs, {"status": "calculated" if len(vals) == 2 else "missing_input"})


def ratio(numerator: Decimal | int | float | str | None, denominator: Decimal | int | float | str | None, *, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    vals = _values((numerator, denominator))
    if len(vals) != 2:
        return CalculationResult("ratio", None, None, source_inputs, {"status": "missing_input"})
    if vals[1] == 0:
        raise ValueError("Ratio denominator cannot be zero")
    return CalculationResult("ratio", vals[0] / vals[1], None, source_inputs, {"status": "calculated"})


def area(length: Decimal | int | float | str | None, width: Decimal | int | float | str | None, *, unit: str = "m", source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    vals = _values((length, width))
    if len(vals) != 2:
        return CalculationResult("area", None, "m2", source_inputs, {"status": "missing_input"})
    if vals[0] < 0 or vals[1] < 0:
        raise ValueError("Area dimensions cannot be negative")
    meters = convert_value(vals[0], unit, "m") * convert_value(vals[1], unit, "m")
    return CalculationResult("area", meters, "m2", source_inputs, {"status": "calculated", "input_unit": unit})


def distance_between_coordinates(lon1: Decimal | float, lat1: Decimal | float, lon2: Decimal | float, lat2: Decimal | float, *, source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    coords = [float(Decimal(str(c))) for c in (lon1, lat1, lon2, lat2)]
    a_lon, a_lat, b_lon, b_lat = coords
    if not all(math.isfinite(c) for c in coords) or abs(a_lon) > 180 or abs(b_lon) > 180 or abs(a_lat) > 90 or abs(b_lat) > 90:
        raise ValueError("Coordinates are outside valid longitude/latitude ranges")
    radius_m = 6371008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    hav = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    distance = Decimal(str(2 * radius_m * math.asin(math.sqrt(min(1.0, hav)))))
    return CalculationResult("distance", distance, "m", source_inputs, {"method": "haversine", "earth_radius_m": str(radius_m)})


def _pair(current, previous, current_unit: str, previous_unit: str) -> tuple[Decimal, Decimal]:
    vals = _values((current, previous))
    if len(vals) != 2:
        raise ValueError("Both calculation inputs are required")
    normalized = convert(vals[1], previous_unit, current_unit)
    return vals[0], normalized


def production_difference(current, previous, *, current_unit: str, previous_unit: str,
                          source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    now, before = _pair(current, previous, current_unit, previous_unit)
    return CalculationResult("production_difference", now - before, current_unit, source_inputs,
        {"formula_id": "production_difference", "formula_version": "1.0", "normalized_previous": str(before)})


def growth_percentage(current, previous, *, current_unit: str, previous_unit: str,
                      source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    now, before = _pair(current, previous, current_unit, previous_unit)
    if before == 0:
        raise ValueError("Growth percentage is undefined when previous production is zero")
    return CalculationResult("growth_percentage", (now - before) / before * 100, "%", source_inputs,
        {"formula_id": "production_growth", "formula_version": "1.0"})


def production_share(component, total_value, *, component_unit: str, total_unit: str,
                     source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    part, whole = _pair(component, total_value, component_unit, total_unit)
    if whole == 0:
        raise ValueError("Production share is undefined when total is zero")
    return CalculationResult("production_share", part / whole * 100, "%", source_inputs,
        {"formula_id": "production_share", "formula_version": "1.0"})


def stripping_ratio(overburden, coal, *, overburden_unit: str, coal_unit: str,
                    source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    numerator, denominator = _pair(overburden, coal, overburden_unit, coal_unit)
    if denominator == 0:
        raise ValueError("Stripping ratio is undefined when coal production is zero")
    return CalculationResult("stripping_ratio", numerator / denominator, "t/t", source_inputs,
        {"formula_id": "stripping_ratio", "formula_version": "1.0"})


def productivity(production, man_shifts, *, production_unit: str, man_shift_unit: str,
                 source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    if unit_dimension(production_unit) != "mass" or unit_dimension(man_shift_unit) != "labor":
        raise ValueError("Productivity requires a mass unit and a man-shift unit")
    vals = _values((production, man_shifts))
    if len(vals) != 2:
        raise ValueError("Production and man-shift inputs are required")
    output, labor = vals
    if labor == 0:
        raise ValueError("Productivity is undefined when man-shifts are zero")
    return CalculationResult("productivity", output / labor, f"{production_unit}/man-shift", source_inputs,
        {"formula_id": "productivity_oms", "formula_version": "1.0"})


def cagr(beginning, ending, years: int | Decimal, *, unit: str,
         source_inputs: tuple[object, ...] = ()) -> CalculationResult:
    start, finish = _values((beginning, ending))
    period = Decimal(str(years))
    if len((start, finish)) != 2 or not period.is_finite() or period <= 0 or start <= 0 or finish < 0:
        raise ValueError("CAGR requires positive beginning value, nonnegative ending value, and positive years")
    normalize_value(start, unit)
    rate = (finish / start) ** (Decimal(1) / period) - Decimal(1)
    return CalculationResult("cagr", rate * 100, "%", source_inputs,
        {"formula_id": "cagr", "formula_version": "1.0", "years": str(period)})


def period_over_period(values: Iterable[Decimal | int | float | str | None], *, unit: str,
                       source_inputs: tuple[object, ...] = ()) -> list[dict]:
    items = _values(values)
    result = []
    for index in range(1, len(items)):
        prior, current = items[index - 1], items[index]
        change = current - prior
        result.append({"previous": str(prior), "current": str(current), "change": str(change),
            "change_percent": str(change / prior * 100) if prior != 0 else None, "unit": unit})
    return result


def statistics_summary(values: Iterable[Decimal | int | float | str | None], *, unit: str | None = None,
                       percentile: Decimal | int | float | None = None) -> dict:
    items = _values(values)
    if not items:
        return {"count": 0, "sum": None, "mean": None, "median": None, "minimum": None,
                "maximum": None, "standard_deviation": None, "percentile": None, "unit": unit,
                "method": "population standard deviation; linear-interpolated percentile; nulls ignored"}
    ordered = sorted(items)
    mean = sum(items, Decimal(0)) / len(items)
    variance = sum(((item - mean) ** 2 for item in items), Decimal(0)) / len(items)
    std = variance.sqrt()
    quantile = None
    if percentile is not None:
        p = Decimal(str(percentile))
        if not p.is_finite() or not 0 <= p <= 100:
            raise ValueError("Percentile must be between 0 and 100")
        position = (len(ordered) - 1) * p / 100
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        quantile = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": len(items), "sum": str(sum(items, Decimal(0))), "mean": str(mean),
            "median": str(Decimal(str(statistics.median(items)))), "minimum": str(ordered[0]),
            "maximum": str(ordered[-1]), "standard_deviation": str(std),
            "percentile": {"requested": str(percentile), "value": str(quantile)} if percentile is not None else None,
            "unit": unit, "method": "population standard deviation; linear-interpolated percentile; nulls ignored"}


def detect_anomalies(values: Iterable[dict], *, method: str = "z_score", threshold: Decimal | float = 3) -> list[dict]:
    rows = [row for row in values if row.get("value") is not None]
    nums = _values(row["value"] for row in rows)
    if not nums:
        return []
    cutoff = Decimal(str(threshold))
    if not cutoff.is_finite() or cutoff <= 0:
        raise ValueError("Anomaly threshold must be a positive finite number")
    if method == "z_score":
        mean = sum(nums, Decimal(0)) / len(nums)
        std = (sum(((n - mean) ** 2 for n in nums), Decimal(0)) / len(nums)).sqrt()
        bounds = (mean - cutoff * std, mean + cutoff * std)
        scores = [(abs(n - mean) / std if std else Decimal(0)) for n in nums]
        context = {"mean": str(mean), "population_standard_deviation": str(std), "bounds": [str(v) for v in bounds]}
    elif method == "iqr":
        ordered = sorted(nums)
        def q(p):
            pos = Decimal(len(ordered) - 1) * p
            lo = int(pos); hi = min(lo + 1, len(ordered) - 1)
            return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
        q1, q3 = q(Decimal("0.25")), q(Decimal("0.75"))
        spread = q3 - q1
        bounds = (q1 - cutoff * spread, q3 + cutoff * spread)
        scores = [max((bounds[0] - n), (n - bounds[1]), Decimal(0)) for n in nums]
        context = {"q1": str(q1), "q3": str(q3), "iqr": str(spread), "bounds": [str(v) for v in bounds]}
    else:
        raise ValueError("Anomaly method must be z_score or iqr")
    return [{**row, "method": method.upper(), "threshold": str(cutoff), "score": str(scores[i]),
             "statistical_context": context, "status": "STATISTICAL_ANOMALY" if nums[i] < bounds[0] or nums[i] > bounds[1] else "NORMAL"}
            for i, row in enumerate(rows)]
