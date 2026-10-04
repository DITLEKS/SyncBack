"""Сквозные сценарии через HTTP на реальных PostgreSQL, Redis и S3-хранилище.

Контрактные тесты проверяют то же API на SQLite, fakeredis и хранилище в памяти;
здесь — то, что эти замены не воспроизводят: presigned URL самого хранилища,
ILIKE по кириллице в PostgreSQL, enum-типы PostgreSQL в массовых UPDATE,
отправку задачи в брокер Celery и хранение refresh-токенов в Redis.
"""

from __future__ import annotations

import uuid

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.audit_log import AuditLog
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AuditAction
from app.infrastructure.storage.s3_storage import S3FileStorage
from tests.integration.conftest import (
    PASSWORD,
    create_project,
    register_and_login,
    seed_review,
    upload_document,
)


async def test_upload_download_delete_roundtrip(
    api_client: AsyncClient,
    pg_sessionmaker: async_sessionmaker[AsyncSession],
    s3_storage: S3FileStorage,
) -> None:
    headers = await register_and_login(api_client)
    project_id = await create_project(api_client, headers)
    content = "Пункт 1. Поставщик обязуется...\n".encode()
    document = await upload_document(api_client, headers, project_id, "договор.txt", content)
    base = f"/api/v1/projects/{project_id}/documents/{document['id']}"

    async with pg_sessionmaker() as session:
        row = await session.get(Document, uuid.UUID(document["id"]))
        assert row is not None
        storage_key = row.storage_key
    assert await s3_storage.exists(storage_key)

    response = await api_client.get(f"{base}/download", headers=headers)
    assert response.status_code == 200, response.text
    async with httpx.AsyncClient() as raw:
        file_response = await raw.get(response.json()["download_url"])
    assert file_response.status_code == 200
    assert file_response.content == content
    assert "attachment" in file_response.headers["content-disposition"]

    async with pg_sessionmaker() as session:
        actions = (
            await session.scalars(
                select(AuditLog.action).where(AuditLog.document_id == uuid.UUID(document["id"]))
            )
        ).all()
    assert actions == [AuditAction.DOWNLOAD]

    response = await api_client.delete(base, headers=headers)
    assert response.status_code == 204, response.text
    assert (await api_client.get(base, headers=headers)).status_code == 404
    assert not await s3_storage.exists(storage_key)


async def test_review_decisions_and_finalize(
    api_client: AsyncClient,
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(api_client)
    project_id = await create_project(api_client, headers)
    document = await upload_document(api_client, headers, project_id, "spec.txt", b"Alpha\n")
    first, second, third = await seed_review(pg_sessionmaker, uuid.UUID(document["id"]), 3)
    suggestions_url = f"/api/v1/projects/{project_id}/documents/{document['id']}/suggestions"

    response = await api_client.patch(
        suggestions_url, json={"ids": [str(first)], "status": "rejected"}, headers=headers
    )
    assert response.status_code == 200, response.text
    response = await api_client.post(f"{suggestions_url}/{first}/reset", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"

    response = await api_client.put(
        f"{suggestions_url}/review",
        json={
            "review_version": 0,
            "decisions": [
                {"suggestion_id": str(first), "decision": "rejected"},
                {"suggestion_id": str(second), "decision": "accepted"},
                {"suggestion_id": str(third), "decision": "rejected"},
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finalized"] is True
    assert body["document_status"] == "ready"
    assert (body["accepted_count"], body["rejected_count"], body["pending_count"]) == (1, 2, 0)

    async with pg_sessionmaker() as session:
        actions = (
            await session.scalars(
                select(AuditLog.action).where(AuditLog.document_id == uuid.UUID(document["id"]))
            )
        ).all()
    assert sorted(a.value for a in actions) == ["accept", "reject", "reject", "reject", "reset"]


async def test_my_documents_search_is_case_insensitive_for_cyrillic(
    api_client: AsyncClient,
) -> None:
    headers = await register_and_login(api_client)
    project_id = await create_project(api_client, headers)
    contract = await upload_document(
        api_client, headers, project_id, "Договор поставки.txt", b"text"
    )
    await upload_document(api_client, headers, project_id, "Отчёт за квартал.txt", b"text")
    await upload_document(api_client, headers, project_id, "100%_готово.txt", b"text")

    response = await api_client.get(
        "/api/v1/documents", params={"search": "ДОГОВОР"}, headers=headers
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [contract["id"]]

    # % и _ в поиске — обычные символы, а не шаблоны LIKE.
    response = await api_client.get("/api/v1/documents", params={"search": "%_"}, headers=headers)
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["items"]] == ["100%_готово.txt"]


async def test_analysis_job_is_sent_to_broker_and_can_be_cancelled(
    api_client: AsyncClient,
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(api_client)
    project_id = await create_project(api_client, headers)
    document = await upload_document(api_client, headers, project_id, "spec.txt", b"Alpha\n")
    jobs_url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"

    response = await api_client.post(jobs_url, headers=headers)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["status"] == "dispatched", job
    assert job["error_code"] is None

    async with pg_sessionmaker() as session:
        row = await session.get(AnalysisJob, uuid.UUID(job["id"]))
        assert row is not None
        assert row.celery_task_id

    doc_url = f"/api/v1/projects/{project_id}/documents/{document['id']}"
    assert (await api_client.get(doc_url, headers=headers)).json()["status"] == "in_progress"

    response = await api_client.post(jobs_url, headers=headers)
    assert response.status_code == 409, response.text

    response = await api_client.delete(f"{jobs_url}/{job['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert (await api_client.get(doc_url, headers=headers)).json()["status"] == "draft"


async def test_refresh_token_is_single_use(api_client: AsyncClient) -> None:
    email = f"it-{uuid.uuid4().hex}@example.com"
    response = await api_client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    response = await api_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    old_refresh = response.json()["refresh_token"]

    response = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200, response.text
    new_refresh = response.json()["refresh_token"]

    response = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 401
    response = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert response.status_code == 200, response.text
