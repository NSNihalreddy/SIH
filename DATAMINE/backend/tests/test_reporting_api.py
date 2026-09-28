from io import BytesIO
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from app.api.dependencies import get_storage_service
from app.api import routes_reports
from app.core.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.reports import Report, ReportArtifact


def test_report_routes_require_auth_and_enforce_generation_role():
    with TestClient(app) as client:
        assert client.get("/api/v1/reports").status_code == 401
    viewer = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True)
    async def current_user(): return viewer
    async def db_session(): yield object()
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/reports", json={"report_type": "PRODUCTION", "title": "Viewer attempt"})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_authenticated_report_download_uses_storage_abstraction_and_audits():
    report_id = uuid.uuid4()
    artifact = ReportArtifact(id=uuid.uuid4(), report_id=report_id, artifact_type="PDF", storage_key="reports/r/pdf", filename="report.pdf",
        mime_type="application/pdf", checksum_sha256="a" * 64, size_bytes=7)
    report = Report(id=report_id, report_type="DOCUMENT_INTELLIGENCE", title="Real report", status="COMPLETED",
        artifacts=[artifact], evidence_references=[], parameters={}, source_document_ids=[], source_version_ids=[], sections=[], provenance={}, validation={})
    class Session:
        def __init__(self): self.calls = 0; self.audit = []
        async def scalar(self, _statement):
            self.calls += 1
            return report if self.calls == 1 else artifact
        def add(self, item): self.audit.append(item)
        async def commit(self): pass
    session = Session()
    storage = SimpleNamespace(download_file=lambda key: BytesIO(b"PDFDATA"))
    actor = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True)
    async def current_user(): return actor
    async def db_session(): yield session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    app.dependency_overrides[get_storage_service] = lambda: storage
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/reports/{report_id}/download?format=PDF")
        assert response.status_code == 200
        assert response.content == b"PDFDATA"
        assert response.headers["content-disposition"].endswith('filename="report.pdf"')
        assert session.audit[-1].action == "REPORT_ARTIFACT_DOWNLOADED"
    finally:
        app.dependency_overrides.clear()


def test_report_creation_is_idempotent_and_routes_once_to_report_queue(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True)
    class Session:
        def __init__(self): self.report = None; self.audit = []
        async def execute(self, _statement, _params=None): return None
        async def scalar(self, _statement): return self.report
        def add(self, item):
            self.audit.append(item)
            if isinstance(item, Report): self.report = item
        async def flush(self): self.report.status = "PENDING"
        async def commit(self): pass
    session = Session()
    calls = []
    monkeypatch.setattr(routes_reports.generate_report_task, "apply_async", lambda **kwargs: calls.append(kwargs))
    async def current_user(): return admin
    async def db_session(): yield session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            first = client.post("/api/v1/reports", headers={"Idempotency-Key": "report-once"},
                                json={"report_type": "DOCUMENT_INTELLIGENCE", "title": "Existing corpus"})
            second = client.post("/api/v1/reports", headers={"Idempotency-Key": "report-once"},
                                 json={"report_type": "DOCUMENT_INTELLIGENCE", "title": "Existing corpus"})
        assert first.status_code == 202 and first.json()["submitted"] is True
        assert second.status_code == 202 and second.json()["idempotent_reuse"] is True
        assert first.json()["report_id"] == second.json()["report_id"]
        assert len(calls) == 1 and calls[0]["queue"] == "reports"
    finally:
        app.dependency_overrides.clear()


def test_report_list_eager_loads_and_serializes_artifacts():
    actor = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True)
    report_id = uuid.uuid4()
    artifact = ReportArtifact(
        id=uuid.uuid4(), report_id=report_id, artifact_type="PDF", storage_key="tests/in-memory/report.pdf",
        filename="report.pdf", mime_type="application/pdf", checksum_sha256="b" * 64, size_bytes=123,
    )
    report = Report(
        id=report_id, report_type="DOCUMENT_INTELLIGENCE", title="In-memory regression fixture",
        description="Not persisted by the test", status="COMPLETED", validation_status="VALID",
        requested_by=actor.id, parameters={}, source_document_ids=[], source_version_ids=[], sections=[],
        evidence_references=[{"source": "test fixture"}], provenance={}, validation={}, artifacts=[artifact],
    )

    class Result:
        def scalars(self): return self
        def unique(self): return self
        def all(self): return [report]

    class Session:
        def __init__(self): self.added = []
        async def scalar(self, _statement): return 1
        async def execute(self, statement):
            assert any(
                getattr(option, "path", None) and any(getattr(path_item, "key", None) == "artifacts" for path_item in option.path)
                for option in statement._with_options
            ), "Report artifacts must be eagerly loaded by the async list query"
            assert statement._limit_clause.value == 4
            assert statement._offset_clause.value == 0
            return Result()
        def add(self, item): self.added.append(item)
        async def commit(self): pass

    session = Session()

    async def current_user(): return actor
    async def db_session(): yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/reports?page=1&page_size=4")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1 and payload["page"] == 1 and payload["page_size"] == 4
        assert len(payload["items"]) == 1
        listed = payload["items"][0]
        assert listed["report_id"] == str(report_id)
        assert listed["report_type"] == "DOCUMENT_INTELLIGENCE"
        assert listed["title"] == "In-memory regression fixture"
        assert listed["status"] == "COMPLETED"
        assert listed["evidence_count"] == 1
        assert listed["artifacts"] == [{
            "artifact_type": "PDF", "filename": "report.pdf", "mime_type": "application/pdf",
            "size_bytes": 123, "checksum_sha256": "b" * 64,
        }]
    finally:
        app.dependency_overrides.clear()
