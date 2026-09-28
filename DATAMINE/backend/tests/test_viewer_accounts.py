import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.auth import hash_password, verify_password
from app.core.config import DATABASE_URL
from app.db.session import get_db
from app.main import app
from app.models.identity import AuditLog, User


@pytest.fixture
def viewer_client():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    admin_username = f"viewer-admin-{admin_id}"
    admin_password = "Viewer-test-admin-password-123!"
    created_user_ids: list[uuid.UUID] = []

    async def setup():
        async with factory() as session:
            session.add(User(
                id=admin_id,
                username=admin_username,
        email=f"{admin_username}@example.com",
                password_hash=hash_password(admin_password),
                full_name="Viewer Test Administrator",
                role="ADMIN",
            ))
            await session.commit()

    async def test_db():
        async with factory() as session:
            yield session

    asyncio.run(setup())
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, admin_username, admin_password, created_user_ids, factory

    async def cleanup():
        async with factory() as session:
            user_ids = [admin_id, *created_user_ids]
            await session.execute(delete(AuditLog).where(
                (AuditLog.actor_id.in_(user_ids)) | (AuditLog.entity_id.in_(user_ids))
            ))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()

    asyncio.run(cleanup())
    asyncio.run(engine.dispose())
    app.dependency_overrides.clear()


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_viewer_register_read_only_and_admin_lifecycle(viewer_client):
    client, admin_username, admin_password, created_user_ids, factory = viewer_client
    username = f"viewer-{uuid.uuid4()}"
    password = "Viewer-test-password-123!"
    registration = {
        "full_name": "Registered Viewer",
        "email": f"{username}@example.com",
        "username": username,
        "password": password,
        "confirm_password": password,
        "role": "ADMIN",
    }
    created = client.post("/api/v1/auth/register", json=registration)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "VIEWER"
    assert body["is_active"] is True
    viewer_id = body["id"]
    created_user_ids.append(uuid.UUID(viewer_id))
    async def stored_password_is_hashed() -> bool:
        async with factory() as session:
            stored = await session.get(User, uuid.UUID(viewer_id))
            assert stored is not None
            return stored.password_hash != password and verify_password(password, stored.password_hash)
    assert asyncio.run(stored_password_is_hashed())

    duplicate = client.post("/api/v1/auth/register", json=registration)
    assert duplicate.status_code == 409
    mismatch = {**registration, "username": f"mismatch-{uuid.uuid4()}", "confirm_password": "Different-password-456!"}
    assert client.post("/api/v1/auth/register", json=mismatch).status_code == 422

    viewer_token = _login(client, username, password)
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}
    current_user = client.get("/api/v1/auth/me", headers=viewer_headers)
    assert current_user.status_code == 200
    assert current_user.json()["role"] == "VIEWER"

    read_results = [
        client.post("/api/v1/search/lexical", json={"query": "coal"}, headers=viewer_headers),
        client.get("/api/v1/analytics/production", headers=viewer_headers),
        client.get("/api/v1/gis/layers", headers=viewer_headers),
        client.get("/api/v1/intelligence/topics", headers=viewer_headers),
        client.get("/api/v1/reports", headers=viewer_headers),
        client.get("/api/v1/audit", headers=viewer_headers),
    ]
    assert [response.status_code for response in read_results] == [200] * len(read_results)

    candidate_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    denied_results = [
        client.post("/api/v1/documents/upload", files={"file": ("viewer.pdf", b"%PDF-1.7", "application/pdf")}, headers=viewer_headers),
        client.post(f"/api/v1/processing/{document_id}/process", headers=viewer_headers),
        client.post(f"/api/v1/verification/candidates/{candidate_id}/claim", headers=viewer_headers),
        client.post(f"/api/v1/verification/candidates/{candidate_id}/approve", json={"reason": "not permitted"}, headers=viewer_headers),
        client.post(f"/api/v1/verification/candidates/{candidate_id}/reject", json={"reason": "not permitted"}, headers=viewer_headers),
        client.post(f"/api/v1/verification/candidates/{candidate_id}/mark-unresolved", json={"reason": "not permitted"}, headers=viewer_headers),
        client.post(f"/api/v1/canonicalization/candidates/{candidate_id}", headers=viewer_headers),
        client.post("/api/v1/analytics/calculate", json={"calculation_type": "sum", "source_record_ids": [str(uuid.uuid4())]}, headers=viewer_headers),
        client.get("/api/v1/auth/users/viewers", headers=viewer_headers),
    ]
    assert [response.status_code for response in denied_results] == [403] * len(denied_results)

    admin_token = _login(client, admin_username, admin_password)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    provisioned_username = f"provisioned-verifier-{uuid.uuid4()}"
    provisioned = client.post("/api/v1/auth/users", headers=admin_headers, json={
        "username": provisioned_username,
        "email": f"{provisioned_username}@example.com",
        "password": "Verifier-test-password-123!",
        "full_name": "Existing Role Regression",
        "role": "VERIFIER",
    })
    assert provisioned.status_code == 201, provisioned.text
    created_user_ids.append(uuid.UUID(provisioned.json()["id"]))
    assert provisioned.json()["role"] == "VERIFIER"
    verifier_headers = {"Authorization": f"Bearer {_login(client, provisioned_username, 'Verifier-test-password-123!')}"}
    assert client.get("/api/v1/verification/queue", headers=verifier_headers).status_code == 200

    listed = client.get("/api/v1/auth/users/viewers", headers=admin_headers)
    assert listed.status_code == 200
    account = next(item for item in listed.json()["items"] if item["id"] == viewer_id)
    assert account["email"] == registration["email"]
    assert "password" not in account and "password_hash" not in account
    detail = client.get(f"/api/v1/auth/users/viewers/{viewer_id}", headers=admin_headers)
    assert detail.status_code == 200

    disabled = client.patch(f"/api/v1/auth/users/viewers/{viewer_id}", json={"is_active": False}, headers=admin_headers)
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert client.post("/api/v1/auth/token", data={"username": username, "password": password}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=viewer_headers).status_code == 401

    enabled = client.patch(f"/api/v1/auth/users/viewers/{viewer_id}", json={"is_active": True}, headers=admin_headers)
    assert enabled.status_code == 200 and enabled.json()["is_active"] is True
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_login(client, username, password)}"}).status_code == 200


def test_admin_viewer_management_is_forbidden_to_other_roles(viewer_client):
    client, _, _, created_user_ids, _ = viewer_client
    response = client.post("/api/v1/auth/register", json={
        "username": f"viewer-{uuid.uuid4()}",
        "email": f"viewer-{uuid.uuid4()}@example.com",
        "password": "Viewer-test-password-123!",
        "confirm_password": "Viewer-test-password-123!",
    })
    assert response.status_code == 201, response.text
    created_user_ids.append(uuid.UUID(response.json()["id"]))
    token = _login(client, response.json()["username"], "Viewer-test-password-123!")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/users/viewers", headers=headers).status_code == 403
    assert client.patch(f"/api/v1/auth/users/viewers/{response.json()['id']}", json={"is_active": False}, headers=headers).status_code == 403
