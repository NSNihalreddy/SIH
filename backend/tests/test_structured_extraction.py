from decimal import Decimal
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import DATABASE_URL
from app.models.documents import Document, DocumentPage, DocumentVersion
from app.models.extraction import ExtractionCandidate
from app.models.intelligence import ExtractedContent, ExtractedTable, ExtractedTableCell
from app.services.structured_extraction import extract_document_version
from app.analytics.deterministic import area, average, difference, distance_between_coordinates, percentage, ratio, total
from app.services.structured_extraction import (
    convert_value,
    coordinate_validation,
    coordinate_point_ewkt,
    entity_match_status,
    extract_candidates,
    extract_production_table_rows,
    extract_spatial_table_rows,
    normalize_unit,
    parse_coordinate_pair,
    reconstruct_table_rows,
)


def test_number_unit_and_unknown_unit_extraction():
    found = extract_candidates("Production: 1,250 tonnes; depth 12.5 metres; grade 8 furlongs")
    measurements = [item for item in found if item["candidate_type"] in {"measurement", "production_record"}]
    assert any(item["raw_value"] == "1,250" and item["unit"] == "t" for item in measurements)
    assert any(item["raw_value"] == "12.5" and item["unit"] == "m" for item in measurements)
    unknown = next(item for item in measurements if item["raw_value"] == "8")
    assert unknown["normalized_value"] is None
    assert unknown["metadata"]["unit_unresolved"] is True


def test_unit_conversion_and_ambiguity():
    assert convert_value("2", "kilotonnes", "tonnes") == Decimal("2000")
    assert convert_value("1.5", "km", "m") == Decimal("1500.0")
    assert normalize_unit("unknown") is None
    assert normalize_unit("mt") is None
    assert normalize_unit("Mt").canonical == "Mt"
    with pytest.raises(ValueError):
        convert_value(1, "m", "ha")


@pytest.mark.parametrize(("raw", "expected"), [
    ("20.5, 11.25", (Decimal("20.5"), Decimal("11.25"))),
    ("11°15'0\"N 20°30'0\"E", (Decimal("20.5"), Decimal("11.25"))),
    ("11°15'N, 20°30'E", (Decimal("20.5"), Decimal("11.25"))),
    ("11°15′N 20°30′E", (Decimal("20.5"), Decimal("11.25"))),
])
def test_coordinate_parse(raw, expected):
    assert parse_coordinate_pair(raw) == expected


def test_coordinate_validation_rejects_out_of_range():
    assert coordinate_validation(Decimal("181"), Decimal("20"))[0]["status"] == "FAILED"
    assert coordinate_validation(Decimal("77"), Decimal("20"))[0]["status"] == "PASSED"
    assert coordinate_point_ewkt(Decimal("77"), Decimal("20")) == "SRID=4326;POINT(77 20)"
    with pytest.raises(ValueError):
        coordinate_point_ewkt(Decimal("181"), Decimal("20"))


def test_table_row_reconstruction_preserves_cells_and_headers():
    rows = reconstruct_table_rows([
        {"row_index": 0, "column_index": 0, "raw_value": "Mine"},
        {"row_index": 0, "column_index": 1, "raw_value": "Output (tonnes)"},
        {"row_index": 1, "column_index": 0, "raw_value": "Example Mine"},
        {"row_index": 1, "column_index": 1, "raw_value": "125"},
    ])
    assert rows[0]["values_by_header"] == {"Mine": "Example Mine", "Output (tonnes)": "125"}
    assert rows[0]["cells"][1]["raw_value"] == "125"


def test_production_table_extracts_source_backed_stacked_headers_and_requires_unit_definition():
    # Values reproduce the All India row in the existing Coal Directory source table.
    table_id = uuid4()
    cells = [
        {"id": "title", "row_index": 0, "column_index": 0, "raw_value": "Production of Raw Coal in 2024-25 (MT)"},
        {"id": "title2", "row_index": 0, "column_index": 1, "raw_value": ""},
        {"id": "title3", "row_index": 0, "column_index": 2, "raw_value": ""},
        {"id": "title4", "row_index": 0, "column_index": 3, "raw_value": ""},
        {"id": "sector", "row_index": 1, "column_index": 0, "raw_value": "Sector"},
        {"id": "coking", "row_index": 1, "column_index": 1, "raw_value": "Coking"},
        {"id": "noncoking", "row_index": 1, "column_index": 2, "raw_value": "Non-Coking"},
        {"id": "total", "row_index": 1, "column_index": 3, "raw_value": "Total Coal"},
        {"id": "all-india", "row_index": 2, "column_index": 0, "raw_value": "All India"},
        {"id": "all-coking", "row_index": 2, "column_index": 1, "raw_value": "66.470"},
        {"id": "all-noncoking", "row_index": 2, "column_index": 2, "raw_value": "981.053"},
        {"id": "all-total", "row_index": 2, "column_index": 3, "raw_value": "1047.523"},
    ]
    rows = reconstruct_table_rows(cells)
    no_definition = extract_production_table_rows(rows, table_id)
    assert no_definition == []

    found = extract_production_table_rows(rows, table_id, mass_unit_definitions={
        "MT": {"canonical": "Mt", "definition": "MT = Million Tonnes", "source_page_id": "glossary-page"}
    })
    total = next(item for item in found if item["source_cell_id"] == "all-total")
    assert total["raw_value"] == "1047.523"
    assert total["normalized_numeric_value"] == Decimal("1047523000.000000")
    assert total["unit"] == "t"
    assert total["metadata"]["reporting_period"] == "2024-25"
    assert total["metadata"]["commodity"] == "Total Coal"
    assert total["metadata"]["unit_definition"] == "MT = Million Tonnes"
    assert total["source_cell_id"] == "all-total"


def test_spatial_rows_pair_labeled_coordinates_and_keep_source_cells():
    cells = [
        {"id": "type", "row_index": 0, "column_index": 0, "raw_value": "Entity Type"},
        {"id": "name", "row_index": 0, "column_index": 1, "raw_value": "Name"},
        {"id": "lat", "row_index": 0, "column_index": 2, "raw_value": "Latitude"},
        {"id": "lon", "row_index": 0, "column_index": 3, "raw_value": "Longitude"},
        {"id": "kind1", "row_index": 1, "column_index": 0, "raw_value": "BOREHOLE"},
        {"id": "name1", "row_index": 1, "column_index": 1, "raw_value": "BH-01"},
        {"id": "lat1", "row_index": 1, "column_index": 2, "raw_value": "23.74"},
        {"id": "lon1", "row_index": 1, "column_index": 3, "raw_value": "86.41"},
        {"id": "kind2", "row_index": 2, "column_index": 0, "raw_value": "VERIFIED_COORDINATE"},
        {"id": "name2", "row_index": 2, "column_index": 1, "raw_value": "Mine Point A"},
        {"id": "lat2", "row_index": 2, "column_index": 2, "raw_value": "23.748"},
        {"id": "lon2", "row_index": 2, "column_index": 3, "raw_value": "86.412"},
    ]
    rows = reconstruct_table_rows(cells)
    found = extract_spatial_table_rows(rows, uuid4())
    assert [item["candidate_type"] for item in found] == ["borehole", "coordinate"]
    assert found[0]["raw_value"] == "BH-01"
    assert found[0]["metadata"]["latitude"] == "23.74"
    assert found[0]["metadata"]["longitude"] == "86.41"
    coordinate = found[1]
    assert coordinate["raw_value"] == "86.412,23.748"
    assert coordinate["metadata"]["coordinate_system"] == "EPSG:4326"
    assert coordinate["validation"][0]["status"] == "PASSED"
    assert coordinate["metadata"]["source_cells"]["Latitude"] == "lat2"
    assert coordinate["metadata"]["source_cells"]["Longitude"] == "lon2"
    assert coordinate["source_cell_id"] == "name2"


def test_entity_candidate_and_match_statuses():
    found = extract_candidates("Mine: North Ridge Colliery; borehole BH-01; Seam: S2")
    types = {item["candidate_type"] for item in found}
    assert {"mine", "borehole", "seam"}.issubset(types)
    assert entity_match_status("Mine A", ["Mine-A"]) == "MATCHED"
    assert entity_match_status("Mine A Colliery", ["Mine A"]) == "POSSIBLE_MATCH"
    assert entity_match_status("Different", ["Mine A"]) == "UNRESOLVED"


def test_null_invalid_values_and_dates():
    assert average([None, None]).value is None
    assert total([None, "2"]).value == Decimal("2")
    assert extract_candidates("Date: 31/02/2024")[0]["normalized_value"] is None
    bad_production = next(item for item in extract_candidates("Production: -2 tonnes") if item["candidate_type"] == "production_record")
    assert bad_production["validation"][0]["status"] == "FAILED"
    with pytest.raises(ValueError):
        total(["NaN"])


def test_deterministic_calculations_and_metadata():
    assert percentage(25, 100).value == Decimal("25.00")
    assert difference(8, 3, source_inputs=("a", "b")).source_inputs == ("a", "b")
    assert ratio(10, 2).value == Decimal("5")
    assert area(2, 3).value == Decimal("6")
    assert distance_between_coordinates(0, 0, 0, 1).value > Decimal("111000")
    assert percentage(None, 100).value is None
    with pytest.raises(ValueError):
        ratio(1, 0)


def test_database_persistence_is_idempotent_and_pending():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    document_id = uuid4()
    version_id = uuid4()
    page_id = uuid4()

    async def run():
        async with factory() as session:
            session.add(Document(id=document_id, original_filename="step6-test.pdf", document_type="pdf", mime_type="application/pdf", file_size=1, storage_key=f"tests/{document_id}", status="UPLOADED"))
            session.add(DocumentVersion(id=version_id, document_id=document_id, version_number=1, storage_key=f"tests/{document_id}", file_size=1))
            session.add(DocumentPage(id=page_id, document_version_id=version_id, page_number=1, text="Mine: Synthetic Ridge\nProduction 125 tonnes\nCoordinates 77.2, 20.1"))
            session.add(ExtractedContent(document_page_id=page_id, content_type="text", text="Mine: Synthetic Ridge\nProduction 125 tonnes\nCoordinates 77.2, 20.1"))
            await session.commit()
            first = await extract_document_version(session, document_id)
            first_rows = list((await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id))).scalars().all())
            second = await extract_document_version(session, document_id)
            second_rows = list((await session.execute(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id))).scalars().all())
            assert first["candidate_count"] > 0
            assert second["candidate_count"] == 0
            assert {row.id for row in first_rows} == {row.id for row in second_rows}
            assert all(row.verification_status == "PENDING" for row in second_rows)
            assert all(row.document_version_id == version_id and row.source_page_id == page_id for row in second_rows)
            assert any(row.source_content_id is not None for row in second_rows)
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()

    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())


def test_spatial_table_extraction_persists_pending_coordinate_candidates_idempotently():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    document_id, version_id, page_id, table_id = uuid4(), uuid4(), uuid4(), uuid4()
    source_cells = [
        ("Entity Type", "Name", "Latitude", "Longitude"),
        ("VERIFIED_COORDINATE", "Mine Point A", "23.748", "86.412"),
    ]

    async def run():
        async with factory() as session:
            session.add(Document(id=document_id, original_filename="gis-row-extraction-test.xlsx", document_type="xlsx", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_size=1, storage_key=f"tests/{document_id}", status="UPLOADED"))
            session.add(DocumentVersion(id=version_id, document_id=document_id, version_number=1, storage_key=f"tests/{document_id}", file_size=1))
            session.add(DocumentPage(id=page_id, document_version_id=version_id, page_number=1))
            session.add(ExtractedTable(id=table_id, document_page_id=page_id, table_order=1))
            await session.flush()
            for row_index, row in enumerate(source_cells):
                for column_index, value in enumerate(row):
                    session.add(ExtractedTableCell(table_id=table_id, row_index=row_index,
                        column_index=column_index, raw_value=value))
            await session.commit()
            first = await extract_document_version(session, document_id)
            candidate = await session.scalar(select(ExtractionCandidate).where(
                ExtractionCandidate.document_version_id == version_id,
                ExtractionCandidate.candidate_type == "coordinate"))
            assert candidate is not None
            assert candidate.verification_status == "PENDING"
            assert candidate.candidate_metadata["latitude"] == "23.748"
            assert candidate.candidate_metadata["longitude"] == "86.412"
            assert candidate.source_table_id == table_id
            assert candidate.source_cell_id is not None
            second = await extract_document_version(session, document_id)
            same = await session.get(ExtractionCandidate, candidate.id)
            assert first["candidate_count"] >= 1 and second["candidate_count"] == 0
            assert same is not None and same.verification_status == "PENDING"
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()

    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())
