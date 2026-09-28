import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_storage_service
from app.core.auth import get_current_user
from app.core.config import DATABASE_URL
from app.db.session import get_db
from app.main import app
from app.models.documents import Document, DocumentVersion
from app.services.document_service import HashingLimitedReader
from app.storage.base import StorageError
from app.storage.local import LocalStorageService


def office_file(document_type: str) -> bytes:
    main_part = "word/document.xml" if document_type == "docx" else "xl/workbook.xml"
    content_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        if document_type == "docx"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types><Override PartName="/{main_part}" ContentType="{content_type}"/></Types>')
        archive.writestr(main_part, "<document/>" if document_type == "docx" else "<workbook/>")
    return output.getvalue()


@pytest.fixture
def client(tmp_path: Path):
    storage = LocalStorageService(tmp_path / "objects")
    test_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def test_db():
        async with test_session_factory() as session:
            yield session

    async def test_user():
        return SimpleNamespace(id=UUID(int=1), username="test-admin", role="ADMIN", is_active=True)

    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_current_user] = test_user
    with TestClient(app) as test_client:
        test_client.storage = storage
        test_client.created_document_ids = []
        yield test_client

    document_ids = list(getattr(test_client, "created_document_ids", []))
    if document_ids:
        async def cleanup() -> None:
            async with test_session_factory() as session:
                docs = (await session.execute(select(Document).where(Document.id.in_(document_ids)))).scalars().all()
                versions = (await session.execute(select(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids)))).scalars().all()
                for key in {item.storage_key for item in [*docs, *versions]}:
                    storage.delete_file(key)
                await session.execute(delete(Document).where(Document.id.in_(document_ids)))
                await session.commit()

        asyncio.run(cleanup())
    asyncio.run(test_engine.dispose())
    app.dependency_overrides.clear()


def post_file(client: TestClient, filename: str, payload: bytes, mime: str):
    response = client.post("/api/v1/documents/upload", files={"file": (filename, payload, mime)})
    if response.status_code == 201:
        client.created_document_ids.append(response.json()["id"])
    return response


def test_document_and_extraction_routes_require_authentication(client: TestClient):
    auth_dependency = app.dependency_overrides.pop(get_current_user)
    try:
        assert client.get("/api/v1/documents").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}/versions").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}/download").status_code == 401
        assert client.post("/api/v1/documents/upload", files={"file": ("auth.pdf", b"%PDF-1.7", "application/pdf")}).status_code == 401
        assert client.post(f"/api/v1/documents/{UUID(int=0)}/extract").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}/extractions").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}/pages/{UUID(int=0)}/extractions").status_code == 401
        assert client.get(f"/api/v1/extractions/{UUID(int=0)}").status_code == 401
        assert client.get(f"/api/v1/documents/{UUID(int=0)}/validation-results").status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = auth_dependency


def test_viewer_can_read_documents_but_cannot_upload(client: TestClient):
    async def viewer():
        return SimpleNamespace(id=UUID(int=2), username="test-viewer", role="VIEWER", is_active=True)

    app.dependency_overrides[get_current_user] = viewer
    assert client.get("/api/v1/documents").status_code == 200
    denied = client.post("/api/v1/documents/upload", files={"file": ("viewer.pdf", b"%PDF-1.7", "application/pdf")})
    assert denied.status_code == 403


def test_valid_pdf_upload_and_metadata_persisted(client: TestClient):
    payload = b"%PDF-1.7\nsynthetic upload test fixture\n%%EOF"
    response = post_file(client, "report.pdf", payload, "application/pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "UPLOADED"
    assert body["file_size"] == len(payload)
    assert body["sha256_checksum"] == hashlib.sha256(payload).hexdigest()
    stored = client.storage.download_file(f"documents/{body['id']}/versions/1/report.pdf")
    try:
        assert stored.read() == payload
    finally:
        stored.close()


def test_valid_docx_upload(client: TestClient):
    response = post_file(client, "report.docx", office_file("docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.status_code == 201


def test_valid_xlsx_upload(client: TestClient):
    response = post_file(client, "report.xlsx", office_file("xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.status_code == 201


def test_invalid_extension(client: TestClient):
    response = post_file(client, "payload.exe", b"MZ\x00", "application/octet-stream")
    assert response.status_code == 415


def test_invalid_mime_type(client: TestClient):
    response = post_file(client, "payload.pdf", b"%PDF-1.7\n", "application/octet-stream")
    assert response.status_code == 415


def test_filename_and_mime_cannot_spoof_file_content(client: TestClient):
    response = post_file(client, "payload.pdf", b"not a PDF", "application/pdf")
    assert response.status_code == 415


def test_empty_file(client: TestClient):
    response = post_file(client, "empty.pdf", b"", "application/pdf")
    assert response.status_code == 400


def test_oversized_file(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import app.api.routes_documents as route_module
    import app.services.document_service as service_module

    monkeypatch.setattr(route_module, "MAX_UPLOAD_SIZE_BYTES", 16)
    monkeypatch.setattr(service_module, "MAX_UPLOAD_SIZE_BYTES", 16)
    response = post_file(client, "large.pdf", b"%PDF-1.7\n" + b"x" * 20, "application/pdf")
    assert response.status_code == 413


def test_sha256_is_calculated_from_file_content():
    payload = b"content whose digest is calculated locally"
    reader = HashingLimitedReader(BytesIO(payload), len(payload))
    assert reader.read(8) + reader.read() == payload
    assert reader.sha256.hexdigest() == hashlib.sha256(payload).hexdigest()


def test_document_record_is_retrievable(client: TestClient):
    response = post_file(client, "record.pdf", b"%PDF-1.7\nrecord fixture", "application/pdf")
    document_id = response.json()["id"]
    stored = client.get(f"/api/v1/documents/{document_id}")
    assert stored.status_code == 200
    assert stored.json()["id"] == document_id


def test_document_list_returns_uploaded_record(client: TestClient):
    response = post_file(client, "listed.pdf", b"%PDF-1.7\nlist fixture", "application/pdf")
    document_id = response.json()["id"]
    listed = client.get("/api/v1/documents")
    assert listed.status_code == 200
    assert any(item["id"] == document_id for item in listed.json())


def test_upload_creates_version_one(client: TestClient):
    response = post_file(client, "version.pdf", b"%PDF-1.7\nversion fixture", "application/pdf")
    versions = client.get(f"/api/v1/documents/{response.json()['id']}/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert versions.json()[0]["version_number"] == 1


def test_download_returns_original_bytes(client: TestClient):
    payload = b"%PDF-1.7\ndownload fixture"
    response = post_file(client, "download.pdf", payload, "application/pdf")
    downloaded = client.get(f"/api/v1/documents/{response.json()['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == payload


def test_download_reports_missing_storage_object(client: TestClient):
    response = post_file(client, "missing-object.pdf", b"%PDF-1.7\nmissing object fixture", "application/pdf")
    document_id = response.json()["id"]
    key = f"documents/{document_id}/versions/1/missing-object.pdf"
    client.storage.delete_file(key)
    missing = client.get(f"/api/v1/documents/{document_id}/download")
    assert missing.status_code == 404


def test_missing_document_returns_404(client: TestClient):
    response = client.get(f"/api/v1/documents/{UUID(int=0)}")
    assert response.status_code == 404


def test_duplicate_upload_returns_conflict_without_second_version(client: TestClient):
    payload = b"%PDF-1.7\nduplicate fixture"
    first = post_file(client, "first.pdf", payload, "application/pdf")
    assert first.status_code == 201, first.text
    duplicate = post_file(client, "copy.pdf", payload, "application/pdf")
    assert duplicate.status_code == 409
    versions = client.get(f"/api/v1/documents/{first.json()['id']}/versions")
    assert len(versions.json()) == 1
    assert len(list((client.storage._root / "documents").rglob("*.*"))) == 1


def test_path_traversal_filename_is_reduced_to_safe_basename(client: TestClient):
    response = post_file(client, "../../escape.pdf", b"%PDF-1.7\ntraversal fixture", "application/pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "escape.pdf"
    assert ".." not in str(client.storage._root / f"documents/{body['id']}/versions/1/escape.pdf")


def test_local_storage_cleans_partial_object_after_read_failure(tmp_path: Path):
    class FailingReader:
        calls = 0

        def read(self, size=-1):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("synthetic storage failure")

    storage = LocalStorageService(tmp_path / "storage")
    with pytest.raises(StorageError):
        storage.upload_file(FailingReader(), "documents/test/versions/1/test.pdf", "application/pdf")
    assert not (tmp_path / "storage" / "documents" / "test" / "versions" / "1" / "test.pdf").exists()


def test_database_failure_cleans_uploaded_object(client: TestClient):
    class EmptyResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    class FailingSession:
        @staticmethod
        async def execute(*args, **kwargs):
            return EmptyResult()

        @staticmethod
        def add(_record):
            return None

        @staticmethod
        async def commit():
            raise RuntimeError("synthetic database write failure")

        @staticmethod
        async def rollback():
            return None

    async def failing_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = failing_db
    response = post_file(client, "db-failure.pdf", b"%PDF-1.7\ndatabase failure fixture", "application/pdf")
    assert response.status_code == 500
    assert not list(client.storage._root.rglob("*.pdf"))


def test_storage_rejects_path_traversal_key(tmp_path: Path):
    storage = LocalStorageService(tmp_path / "storage")
    with pytest.raises(StorageError):
        storage.exists("../../outside")
