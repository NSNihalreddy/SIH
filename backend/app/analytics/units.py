"""Explicit, versioned unit normalization for deterministic mining analytics."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


RULE_VERSION = "units-1.0"


@dataclass(frozen=True)
class UnitDefinition:
    normalized_unit: str
    dimension: str
    factor_to_base: Decimal


_DEFINITIONS: dict[str, UnitDefinition] = {}


def _register(canonical: str, dimension: str, factor: str, *aliases: str) -> None:
    item = UnitDefinition(canonical, dimension, Decimal(factor))
    for alias in (canonical, *aliases):
        _DEFINITIONS[alias.casefold().strip()] = item


_register("t", "mass", "1", "tonne", "tonnes", "metric tonne", "metric tonnes")
_register("kg", "mass", "0.001", "kilogram", "kilograms")
_register("Mt", "mass", "1000000", "million tonne", "million tonnes", "million metric tonnes")
_register("Rs", "currency", "1", "inr", "rupee", "rupees", "₹")
_register("million Rs", "currency", "1000000", "million inr", "million rupees")
_register("man-shift", "labor", "1", "man shift", "man-shifts", "manshift", "man days")
_register("%", "percentage", "1", "percent", "percentage")


def _resolve(unit: str) -> UnitDefinition:
    # MT is commonly used both for metric tonnes and million tonnes. Refuse to guess.
    if unit.strip().casefold() == "mt" and unit.strip() != "Mt":
        raise ValueError("Unit 'MT' is ambiguous; specify 'tonnes' or 'Mt'")
    found = _DEFINITIONS.get(unit.casefold().strip())
    if found is None:
        raise ValueError(f"Unsupported or ambiguous unit: {unit}")
    return found


def unit_dimension(unit: str) -> str:
    return _resolve(unit).dimension


def normalize_value(value: Decimal | int | float | str | None, unit: str | None) -> dict:
    if value is None or unit is None or not str(unit).strip():
        raise ValueError("A numeric value and explicit unit are required")
    try:
        original = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Value must be numeric") from exc
    if not original.is_finite():
        raise ValueError("Value must be finite")
    definition = _resolve(unit)
    base_unit = {"mass": "t", "currency": "Rs", "labor": "man-shift", "percentage": "%"}[definition.dimension]
    normalized = original * definition.factor_to_base
    return {
        "original_value": str(original), "original_unit": unit,
        "normalized_value": str(normalized), "normalized_unit": base_unit,
        "dimension": definition.dimension, "conversion_rule": RULE_VERSION,
    }


def convert(value: Decimal | int | float | str, from_unit: str, to_unit: str) -> Decimal:
    source, target = _resolve(from_unit), _resolve(to_unit)
    if source.dimension != target.dimension:
        raise ValueError(f"Incompatible units: {from_unit} and {to_unit}")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("Value must be finite")
    return amount * source.factor_to_base / target.factor_to_base
