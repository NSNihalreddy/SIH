import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.analytics.deterministic import (cagr, detect_anomalies, growth_percentage,
    period_over_period, productivity, production_difference, production_share, statistics_summary, stripping_ratio)
from app.analytics.units import convert, normalize_value
from app.core.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.services.analytics import calculate_from_rows, cross_source_checks, reporting_year, trend_analysis


def test_production_difference_growth_share_and_zero_denominators():
    assert production_difference(12, 10, current_unit="t", previous_unit="Mt").value == Decimal("-9999988")
    assert growth_percentage(120, 100, current_unit="tonnes", previous_unit="t").value == Decimal("20.0")
    assert production_share(25, 100, component_unit="t", total_unit="tonnes").value == Decimal("25.00")
    with pytest.raises(ValueError, match="zero"):
        growth_percentage(1, 0, current_unit="t", previous_unit="t")
    with pytest.raises(ValueError, match="zero"):
        production_share(1, 0, component_unit="t", total_unit="t")
    with pytest.raises(ValueError, match="required"):
        production_difference(None, 1, current_unit="t", previous_unit="t")


def test_stripping_ratio_productivity_and_cagr():
    assert stripping_ratio(400, 100, overburden_unit="t", coal_unit="tonnes").value == Decimal("4")
    assert productivity(600, 30, production_unit="t", man_shift_unit="man-shift").value == Decimal("20")
    assert cagr(100, 121, 2, unit="t").value == Decimal("10.0")
    with pytest.raises(ValueError):
        productivity(1, 0, production_unit="t", man_shift_unit="man-shift")
    with pytest.raises(ValueError):
        cagr(0, 1, 2, unit="t")


def test_unit_normalization_is_explicit_and_rejects_ambiguous_or_incompatible_units():
    assert normalize_value("2", "million tonnes") == {
        "original_value": "2", "original_unit": "million tonnes", "normalized_value": "2000000",
        "normalized_unit": "t", "dimension": "mass", "conversion_rule": "units-1.0"}
    assert convert(1000, "kg", "t") == Decimal("1.000")
    assert convert(2, "million Rs", "Rs") == Decimal("2000000")
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_value(1, "MT")
    with pytest.raises(ValueError, match="Incompatible"):
        convert(1, "t", "Rs")
    with pytest.raises(ValueError):
        normalize_value(None, "t")


def test_statistics_nulls_percentile_and_empty_input():
    result = statistics_summary([1, None, 3, 5], unit="t", percentile=50)
    assert result["count"] == 3 and result["sum"] == "9"
    assert result["mean"] == "3" and result["median"] == "3"
    assert result["minimum"] == "1" and result["maximum"] == "5"
    assert Decimal(result["standard_deviation"]).quantize(Decimal("0.0001")) == Decimal("1.6330")
    assert result["percentile"]["value"] == "3"
    assert statistics_summary([None])["count"] == 0
    with pytest.raises(ValueError): statistics_summary([1, 2], percentile=101)
    changes = period_over_period([None, 10, 15, 0], unit="t")
    assert Decimal(changes[0]["change"]) == 5 and Decimal(changes[0]["change_percent"]) == 50
    assert Decimal(changes[1]["change"]) == -15 and Decimal(changes[1]["change_percent"]) == -100


def test_z_score_and_iqr_anomalies_are_transparent():
    rows = [{"value": str(n), "provenance": {"id": str(n)}} for n in [1, 2, 3, 4, 100]]
    z = detect_anomalies(rows, method="z_score", threshold=1)
    assert z[-1]["status"] == "STATISTICAL_ANOMALY" and z[-1]["method"] == "Z_SCORE"
    iqr = detect_anomalies(rows, method="iqr", threshold=1.5)
    assert iqr[-1]["status"] == "STATISTICAL_ANOMALY"
    assert Decimal(iqr[-1]["statistical_context"]["q1"]) == Decimal("2")
    with pytest.raises(ValueError): detect_anomalies(rows, method="unknown")


def _trusted_row(record_id, value, period="2024-25", mine_id="mine-1", commodity="coal"):
    return {"record_id": record_id, "mine_id": mine_id, "project_id": None, "mine": "A Mine", "state": "State A",
        "company": "Company A", "project": None, "period": period, "year": reporting_year(period),
        "commodity": commodity, "original_value": str(value), "original_unit": "t", "value": str(value),
        "unit": "t", "conversion_rule": "units-1.0", "verification_id": f"verify-{record_id}",
        "provenance": {"id": record_id, "source_document_id": f"doc-{record_id}"}}


def test_cross_source_validation_detects_duplicate_and_conflict_with_provenance():
    duplicate_rows = [_trusted_row("a", 10), _trusted_row("b", 10)]
    conflict_rows = [_trusted_row("c", 10), _trusted_row("d", 15)]
    duplicate = cross_source_checks(duplicate_rows, [])[0]
    conflict = cross_source_checks(conflict_rows, [])[0]
    assert duplicate["validation_type"] == "DUPLICATE_OBSERVATION"
    assert conflict["validation_type"] == "CONFLICTING_PRODUCTION_VALUES"
    assert conflict["difference"] == "5" and len(conflict["provenance"]) == 2


def test_calculation_reproducibility_and_verified_data_requirement():
    records = [_trusted_row("source-a", 100), _trusted_row("source-b", 125, "2025-26")]
    first = calculate_from_rows("growth_percentage", records)
    second = calculate_from_rows("growth_percentage", records)
    assert first == second and first["value"] == "25.00"
    with pytest.raises(LookupError, match="INSUFFICIENT_VERIFIED_DATA"):
        calculate_from_rows("sum", [])
    with pytest.raises(LookupError, match="schema"):
        calculate_from_rows("productivity", records)


def test_trend_results_are_math_based_and_keep_source_provenance():
    rows = [_trusted_row("a", 10, "2022-23"), _trusted_row("b", 20, "2023-24"), _trusted_row("c", 40, "2024-25")]
    result = trend_analysis(rows)
    assert result["status"] == "OK" and [row["year"] for row in result["observations"]] == [2022, 2023, 2024]
    assert result["period_changes"][0]["change"] == "10"
    assert result["period_changes"][0]["change_percent"] == "100"
    assert result["cagr_percent"] is not None
    assert result["observations"][0]["provenance"][0]["source_document_id"] == "doc-a"
    assert trend_analysis([])["status"] == "INSUFFICIENT_VERIFIED_DATA"


def test_analytics_api_requires_authentication_and_role():
    with TestClient(app) as client:
        response = client.get("/api/v1/analytics/production")
    assert response.status_code == 401

    viewer = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True)
    async def current_user(): return viewer
    async def fake_db(): yield object()
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = fake_db
    try:
        with TestClient(app) as client:
            denied = client.post("/api/v1/analytics/calculate", json={
                "calculation_type": "sum", "source_record_ids": [str(uuid.uuid4())]})
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_filter_and_pagination_parameters_are_defined_on_analytics_routes():
    from app.api.routes_analytics import router
    paths = {route.path for route in router.routes}
    assert {"/api/v1/analytics/production", "/api/v1/analytics/trends",
        "/api/v1/analytics/statistics", "/api/v1/analytics/anomalies",
        "/api/v1/analytics/validations", "/api/v1/analytics/calculate"}.issubset(paths)


def test_verified_record_query_applies_filters_and_database_pagination():
    from sqlalchemy.dialects import postgresql
    from app.services.analytics import verified_production

    class Rows:
        def all(self): return []
    class Session:
        statement = None
        async def execute(self, statement):
            self.statement = statement
            return Rows()

    session = Session()
    asyncio.run(verified_production(session, entity_type="MINE", entity_id=uuid.uuid4(),
        year=2024, state="State X", commodity="coal", limit=5, offset=10))
    query = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "trusted_production_records.verification_status" in query
    assert "reporting_period" in query and "CAST" in query
    assert "trusted_mines.state ILIKE" in query and "trusted_production_records.commodity ILIKE" in query
    assert "LIMIT" in query and "OFFSET" in query
