from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import IntelligenceRun
from app.models.documents import Document, DocumentPage
from app.models.identity import AuditLog
from app.models.rag import DocumentChunk
from app.models.reports import Report, ReportArtifact
from app.services.reporting import artifact_metadata, export_docx, export_pdf, export_xlsx, generated_timestamp, validate_report
from app.storage.factory import create_storage_service


def _query_async_db(callback):
    """Use a task-local async engine; Celery tasks do not share event loops."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.config import DATABASE_URL

    async def run():
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await callback(session)
        finally:
            await engine.dispose()
    return asyncio.run(run())


def _document_report(session: Session, report: Report, payload: dict) -> dict:
    run = session.scalar(select(IntelligenceRun).where(IntelligenceRun.scope == "CORPUS", IntelligenceRun.status == "COMPLETED")
                         .order_by(IntelligenceRun.completed_at.desc()).limit(1))
    if not run or not run.result:
        return {"sections": [
            {"title": "Document Overview", "status": "INSUFFICIENT_EVIDENCE", "text": "No completed Step 12 intelligence run is available."},
            {"title": "Topics", "status": "INSUFFICIENT_EVIDENCE", "items": [], "citations": []},
            {"title": "Keywords", "status": "INSUFFICIENT_EVIDENCE", "items": [], "citations": []}],
            "evidence": [], "source_ids": [], "version_ids": [],
            "limitations": ["Document intelligence is not available until a completed intelligence run exists."], "run_id": None}
    selected_ids = set(str(x) for x in (report.parameters.get("document_ids") or []))
    evidence_map = {}
    topics, keywords = [], []
    for item in run.result.get("topics", []):
        refs = [e for e in item.get("evidence", []) if not selected_ids or e.get("document_id") in selected_ids]
        if refs:
            distribution = {str(row["document_id"]): row["frequency"] for row in item.get("distribution", [])
                            if not selected_ids or str(row["document_id"]) in selected_ids}
            topics.append({"topic": item.get("name"), "frequency": item.get("frequency"),
                           "document_frequencies": distribution, "citations": [],
                           "_chunk_ids": [e["chunk_id"] for e in refs[:2]]})
            evidence_map.update({e["chunk_id"]: e for e in refs[:2]})
    for item in run.result.get("keywords", []):
        refs = [e for e in item.get("evidence", []) if not selected_ids or e.get("document_id") in selected_ids]
        if refs:
            keywords.append({"term": item.get("term"), "chunk_frequency": item.get("chunk_frequency"), "citations": [],
                             "_chunk_ids": [e["chunk_id"] for e in refs[:1]]})
            evidence_map.update({e["chunk_id"]: e for e in refs[:1]})
    if selected_ids:
        found = set((session.scalars(select(Document.id).where(Document.id.in_([uuid.UUID(value) for value in selected_ids])))).all())
        if {uuid.UUID(value) for value in selected_ids} - found:
            raise ValueError("One or more selected source documents do not exist")
    source_chunk_ids = [uuid.UUID(value) for value in evidence_map]
    chunks = session.execute(select(DocumentChunk, Document.original_filename, DocumentPage.page_number)
        .join(Document, Document.id == DocumentChunk.document_id)
        .outerjoin(DocumentPage, DocumentPage.id == DocumentChunk.document_page_id)
        .where(
            DocumentChunk.id.in_(source_chunk_ids),
            DocumentChunk.is_indexed.is_(True),
        )
        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)).all() if source_chunk_ids else []
    evidence, citations_by_chunk = [], {}
    for chunk, filename, page_number in chunks:
        ref = evidence_map[str(chunk.id)]
        citation = f"E{len(evidence)+1}"
        item = {"citation_id": citation, "document_id": str(chunk.document_id), "document_name": filename,
            "document_version_id": str(chunk.document_version_id), "page_id": str(chunk.document_page_id) if chunk.document_page_id else None,
            "page_number": page_number if page_number is not None else ref.get("page_number"),
            "source_unit_id": str(chunk.source_unit_id) if chunk.source_unit_id else None, "chunk_id": str(chunk.id),
            "evidence_type": chunk.evidence_type, "text": chunk.text[:1200]}
        evidence.append(item); citations_by_chunk[str(chunk.id)] = citation
    # Cite each derived topic/keyword only with its exact retained source chunks.
    for collection in (topics, keywords):
        for row in collection:
            row["citations"] = [citations_by_chunk[chunk_id] for chunk_id in row.pop("_chunk_ids", []) if chunk_id in citations_by_chunk]
    topics = [row for row in topics if row["citations"]]
    keywords = [row for row in keywords if row["citations"]]
    sections = [
        {"title": "Document Overview", "text": f"Deterministic Step 12 intelligence derived from {run.source_chunk_count} valid indexed chunks across {run.result.get('source_count', 0)} source document(s). Intelligence run {run.id}.", "citations": [],
         "provenance": {"intelligence_run_id": str(run.id), "source_chunk_count": run.source_chunk_count}},
        {"title": "Topics", "status": "OK" if topics else "INSUFFICIENT_EVIDENCE", "items": topics, "citations": sorted({c for x in topics for c in x["citations"]})},
        {"title": "Keywords", "status": "OK" if keywords else "INSUFFICIENT_EVIDENCE", "items": keywords, "citations": sorted({c for x in keywords for c in x["citations"]})},
    ]
    if not evidence:
        sections[0]["status"] = "INSUFFICIENT_EVIDENCE"
    source_ids = sorted({item["document_id"] for item in evidence})
    version_ids = sorted({item["document_version_id"] for item in evidence})
    limits = []
    if len(source_ids) < 2:
        limits.append("Only one distinct source document is represented; no multi-document comparison is claimed.")
    return {"sections": sections, "evidence": evidence, "source_ids": source_ids, "version_ids": version_ids,
            "limitations": limits, "run_id": str(run.id)}


def generate_report(report_id: str) -> dict:
    from app.core.config import SYNC_DATABASE_URL
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(SYNC_DATABASE_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    storage = create_storage_service()
    try:
        with factory() as session:
            report = session.scalar(select(Report).where(Report.id == uuid.UUID(str(report_id))).with_for_update())
            if report is None:
                return {"status": "NOT_FOUND", "report_id": report_id}
            if report.status == "COMPLETED":
                return {"status": report.status, "report_id": str(report.id), "idempotent_reuse": True}
            if report.status == "PROCESSING":
                return {"status": report.status, "report_id": str(report.id), "already_processing": True}
            report.status = "PROCESSING"; report.error_message = None; session.commit()
            try:
                sections, evidence, source_ids, version_ids, limitations = [], [], [], [], []
                if report.report_type == "PRODUCTION":
                    from app.services.analytics import verified_production
                    records, issues = _query_async_db(lambda async_session: verified_production(async_session,
                        year=report.parameters.get("year"), state=report.parameters.get("state"),
                        year_from=report.parameters.get("year_from"), year_to=report.parameters.get("year_to"),
                        commodity=report.parameters.get("commodity")))
                    if records:
                        metric_rows = []
                        for row in records:
                            citation = f"E{len(evidence)+1}"
                            metric_rows.append({key: row.get(key) for key in ("record_id", "mine", "project", "state", "period", "commodity", "value", "unit", "verification_id")} | {"citation": citation})
                            evidence.append({"citation_id": citation, "evidence_type": "TRUSTED_PRODUCTION_RECORD",
                                "record_id": row["record_id"], "verification_id": row["verification_id"], "provenance": row["provenance"]})
                            source_document_id = row["provenance"].get("source_document_id")
                            if source_document_id:
                                source_ids.append(source_document_id)
                            source_version_id = row["provenance"].get("source_version_id")
                            if source_version_id:
                                version_ids.append(source_version_id)
                        source_ids, version_ids = sorted(set(source_ids)), sorted(set(version_ids))
                        sections.append({"title": "Production Overview", "status": "OK", "items": metric_rows,
                            "record_count": len(records), "citations": [item["citation_id"] for item in evidence]})
                        from app.services.analytics import cross_source_checks, find_anomalies, trend_analysis
                        trends = trend_analysis(records)
                        sections.append({"title": "Period Comparison", "status": trends["status"],
                            "items": trends["period_changes"], "cagr_percent": trends["cagr_percent"],
                            "method": trends["cagr_method"], "citations": [item["citation_id"] for item in evidence]})
                        sections.append({"title": "Statistics", "status": "OK" if trends["statistics"]["count"] else "INSUFFICIENT_VERIFIED_DATA",
                            "items": [trends["statistics"]], "citations": [item["citation_id"] for item in evidence]})
                        if len(records) >= 3:
                            anomalies = find_anomalies(records, "z_score", 3)
                            sections.append({"title": "Anomalies", "status": "OK", "items": anomalies,
                                "method": "Step 10 z-score with threshold 3; statistical signal only",
                                "citations": [item["citation_id"] for item in evidence]})
                        else:
                            sections.append({"title": "Anomalies", "status": "INSUFFICIENT_DATA",
                                "text": "At least three verified production observations are required for this anomaly method."})
                        checks = cross_source_checks(records, issues)
                        sections.append({"title": "Validations", "status": "OK" if not checks else "WARNING",
                            "items": checks, "citations": [item["citation_id"] for item in evidence]})
                    else:
                        sections.append({"title": "Production Overview", "status": "INSUFFICIENT_VERIFIED_DATA",
                            "text": "No verified production records match the selected parameters. No production figures are reported."})
                        limitations.append("INSUFFICIENT_VERIFIED_DATA: no matching verified production records.")
                    if issues:
                        sections.append({"title": "Validation Issues", "items": issues, "status": "WARNING"})
                else:
                    if report.report_type == "MULTI_DOCUMENT_COMPARISON":
                        requested = list(dict.fromkeys(report.parameters.get("document_ids", [])))
                        distinct = set(requested)
                        if len(distinct) < 2:
                            sections.append({"title": "Multi-document Comparison", "status": "INSUFFICIENT_DOCUMENT_SET",
                                "text": "At least two distinct existing source documents are required; duplicate copies are not compared."})
                            limitations.append("INSUFFICIENT_DOCUMENT_SET: fewer than two distinct documents were selected.")
                        else:
                            built = _document_report(session, report, {"document_ids": requested})
                            sections.extend(built["sections"]); evidence = built["evidence"]
                            source_ids, version_ids = built["source_ids"], built["version_ids"]
                            if len(source_ids) < 2:
                                sections.insert(0, {"title": "Multi-document Comparison", "status": "INSUFFICIENT_DOCUMENT_SET",
                                    "text": "Selected inputs did not resolve to two distinct documents with indexed evidence."})
                            else:
                                topic_section = next((section for section in built["sections"] if section.get("title") == "Topics"), None)
                                comparison_rows = []
                                for row in (topic_section or {}).get("items", []):
                                    frequencies = row.get("document_frequencies", {})
                                    represented = [doc_id for doc_id in source_ids if frequencies.get(doc_id, 0) > 0]
                                    comparison_rows.append({"topic": row["topic"], "classification": "COMMON" if len(represented) == len(source_ids) else "DOCUMENT_SPECIFIC",
                                        "document_frequencies": frequencies, "citations": row.get("citations", [])})
                                sections.insert(0, {"title": "Multi-document Comparison", "status": "COMPLETED",
                                    "text": "Topic frequency comparisons use persisted Step 12 document distributions for distinct selected source documents.",
                                    "items": comparison_rows,
                                    "citations": sorted({citation for row in comparison_rows for citation in row["citations"]})})
                            limitations.extend(built["limitations"])
                    else:
                        built = _document_report(session, report, {})
                        sections.extend(built["sections"]); evidence = built["evidence"]
                        source_ids, version_ids = built["source_ids"], built["version_ids"]
                        limitations.extend(built["limitations"])
                        if report.report_type == "EXECUTIVE_SUMMARY":
                            # Summary wording is deterministic; all figures come from Step 10 and GIS services.
                            sections.insert(0, {"title": "Executive Summary", "text": "This summary reflects only the report sections and evidence listed below. Numerical production and spatial conclusions are omitted when trusted verified inputs are unavailable.", "citations": []})
                            from app.services.analytics import verified_production
                            from app.gis.queries import count_spatial_entities, spatial_entities
                            async def load_exec_metrics(async_session):
                                production, issues = await verified_production(async_session)
                                counts = {kind: await count_spatial_entities(async_session, entity_type=kind)
                                    for kind in ("MINE", "BOREHOLE", "VERIFIED_COORDINATE")}
                                features = await spatial_entities(async_session, limit=100)
                                return production, issues, counts, features
                            production, issues, spatial_counts, features = _query_async_db(load_exec_metrics)
                            if production:
                                metric_rows = []
                                for index, row in enumerate(production, 1):
                                    citation = f"E{len(evidence)+1}"
                                    metric_rows.append({key: row.get(key) for key in ("record_id", "mine", "project", "state", "period", "commodity", "value", "unit", "verification_id")})
                                    evidence.append({"citation_id": citation, "evidence_type": "TRUSTED_PRODUCTION_RECORD",
                                        "record_id": row["record_id"], "verification_id": row["verification_id"],
                                        "provenance": row["provenance"]})
                                sections.append({"title": "Verified Production Metrics", "status": "OK", "items": metric_rows,
                                    "citations": [item["citation_id"] for item in evidence if item.get("evidence_type") == "TRUSTED_PRODUCTION_RECORD"]})
                            else:
                                sections.append({"title": "Verified Production Metrics", "status": "INSUFFICIENT_VERIFIED_DATA",
                                    "text": "No verified production records are available; no production values are reported."})
                                limitations.append("INSUFFICIENT_VERIFIED_DATA: no verified production records.")
                            spatial_items = []
                            for feature in features:
                                citation = f"E{len(evidence)+1}"
                                spatial_items.append({"entity_type": feature["entity_type"], "name": feature["name"],
                                    "entity_id": feature["id"], "verification_id": feature["provenance"].get("verification_id"),
                                    "citation": citation})
                                evidence.append({"citation_id": citation, "evidence_type": "TRUSTED_SPATIAL_ENTITY",
                                    "entity_id": feature["id"], "entity_type": feature["entity_type"],
                                    "provenance": feature["provenance"], "properties": feature["properties"]})
                            spatial_count = sum(spatial_counts.values())
                            if spatial_count:
                                sections.append({"title": "Trusted Spatial Overview", "status": "OK",
                                    "items": [{"entity_type": kind, "verified_count": count} for kind, count in spatial_counts.items()],
                                    "citations": [item["citation"] for item in spatial_items]})
                            else:
                                sections.append({"title": "Trusted Spatial Overview", "status": "INSUFFICIENT_SPATIAL_DATA",
                                    "text": "No verified spatial geometries are available; no spatial findings are reported."})
                                limitations.append("INSUFFICIENT_SPATIAL_DATA: no verified PostGIS geometries.")
                            if spatial_items:
                                sections.append({"title": "Spatial Entity Sources", "items": spatial_items,
                                    "citations": [item["citation"] for item in spatial_items]})
                            if issues:
                                sections.append({"title": "Production Validation Issues", "status": "WARNING", "items": issues})
                            limitations.append("Gemini wording assistance was not requested; this summary is deterministic and evidence-bound.")
                source_ids = sorted(set(source_ids) | {str(item.get("document_id")) for item in evidence if item.get("document_id")}
                    | {str(item.get("provenance", {}).get("source_document_id")) for item in evidence if item.get("provenance", {}).get("source_document_id")})
                version_ids = sorted(set(version_ids) | {str(item.get("document_version_id")) for item in evidence if item.get("document_version_id")}
                    | {str(item.get("provenance", {}).get("source_version_id")) for item in evidence if item.get("provenance", {}).get("source_version_id")})
                payload = {"title": report.title, "description": report.description,
                           "report_type": report.report_type, "generated_at": generated_timestamp(), "parameters": report.parameters,
                           "sections": sections, "evidence": evidence, "limitations": limitations,
                           "validation": {}, "provenance": {"source_document_ids": source_ids,
                           "source_version_ids": version_ids, "intelligence_run_id": built.get("run_id") if report.report_type != "PRODUCTION" and report.report_type != "MULTI_DOCUMENT_COMPARISON" else None}}
                validation = validate_report(payload); payload["validation"] = validation
                if validation["status"] == "VALIDATION_FAILED":
                    report.status = "VALIDATION_FAILED"; report.validation_status = validation["status"]
                    report.sections = sections; report.evidence_references = evidence; report.validation = validation
                    report.error_message = "; ".join(validation["errors"]); report.completed_at = datetime.now(timezone.utc)
                    session.add(AuditLog(actor_id=report.requested_by, action="REPORT_VALIDATION_FAILED", entity_type="REPORT", entity_id=report.id, details=validation, source="report_worker")); session.commit()
                    return {"report_id": str(report.id), "status": report.status, "validation": validation}
                payload["validation"] = validation
                outputs = [("PDF", export_pdf(payload)), ("DOCX", export_docx(payload)), ("XLSX", export_xlsx(payload))]
                created_keys = []
                try:
                    for kind, data in outputs:
                        meta = artifact_metadata(report.id, kind, data)
                        storage_key = f"reports/{report.id}/{kind.lower()}/{meta['filename']}"
                        storage.upload_file(BytesIO(data), storage_key, meta["mime_type"]); created_keys.append(storage_key)
                        session.add(ReportArtifact(report_id=report.id, artifact_type=kind, storage_key=storage_key,
                            filename=meta["filename"], mime_type=meta["mime_type"], checksum_sha256=meta["checksum_sha256"], size_bytes=meta["size_bytes"]))
                        session.add(AuditLog(actor_id=report.requested_by, action="REPORT_ARTIFACT_CREATED", entity_type="REPORT", entity_id=report.id,
                            details={"artifact_type": kind, "checksum_sha256": meta["checksum_sha256"], "size_bytes": meta["size_bytes"]}, source="report_worker"))
                    report.sections = sections; report.evidence_references = evidence; report.source_document_ids = source_ids
                    report.source_version_ids = version_ids; report.provenance = payload["provenance"]
                    report.validation = validation; report.validation_status = validation["status"]
                    report.status = "COMPLETED"; report.completed_at = datetime.now(timezone.utc)
                    session.add(AuditLog(actor_id=report.requested_by, action="REPORT_GENERATION_COMPLETED", entity_type="REPORT", entity_id=report.id,
                        details={"status": report.status, "validation_status": validation["status"], "artifact_types": [x[0] for x in outputs], "evidence_count": len(evidence)}, source="report_worker"))
                    session.add(AuditLog(actor_id=report.requested_by, action="REPORT_VALIDATION_COMPLETED", entity_type="REPORT", entity_id=report.id,
                        details={"validation_status": validation["status"], "validation": validation}, source="report_worker"))
                    session.commit()
                except Exception:
                    session.rollback()
                    for key in created_keys:
                        try: storage.delete_file(key)
                        except Exception: pass
                    raise
                return {"report_id": str(report.id), "status": "COMPLETED", "validation_status": validation["status"],
                        "artifact_types": [x[0] for x in outputs], "evidence_count": len(evidence)}
            except Exception as exc:
                session.rollback()
                failed = session.get(Report, uuid.UUID(str(report_id)))
                if failed:
                    failed.status = "FAILED"; failed.error_message = f"{type(exc).__name__}: {str(exc)[:1000]}"
                    failed.completed_at = datetime.now(timezone.utc)
                    session.add(AuditLog(actor_id=failed.requested_by, action="REPORT_GENERATION_FAILED", entity_type="REPORT", entity_id=failed.id,
                        details={"error_type": type(exc).__name__}, source="report_worker")); session.commit()
                raise
    finally:
        engine.dispose()
