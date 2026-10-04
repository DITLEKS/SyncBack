"""Блок «Недавние документы» дашборда."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.document_open import DocumentOpen
from tests.contract.conftest import create_project, register_and_login, upload_document

pytestmark = pytest.mark.asyncio


async def _mark_opened(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: str,
    document_id: str,
    opened_at: datetime,
) -> None:
    # POST /open использует PostgreSQL ON CONFLICT, которого нет в SQLite контрактных тестов.
    async with sessionmaker() as session:
        session.add(
            DocumentOpen(
                user_id=uuid.UUID(user_id),
                document_id=uuid.UUID(document_id),
                last_opened_at=opened_at,
            )
        )
        await session.commit()


async def test_recent_documents_include_upload_date(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await register_and_login(client)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    project_id = await create_project(client, headers, name="Платёжный шлюз")
    older = await upload_document(client, headers, project_id, filename="older.txt")
    newer = await upload_document(client, headers, project_id, filename="newer.txt")
    now = datetime.now(tz=UTC)
    await _mark_opened(sessionmaker, me["id"], older["id"], now - timedelta(hours=2))
    await _mark_opened(sessionmaker, me["id"], newer["id"], now - timedelta(hours=1))

    response = await client.get("/api/v1/documents/recent", headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["id"] for item in items] == [newer["id"], older["id"]]
    first = items[0]
    assert first["project_name"] == "Платёжный шлюз"
    assert first["status"] == "draft"
    # SQLite теряет часовой пояс при чтении, поэтому сравниваем без него.
    uploaded = datetime.fromisoformat(first["uploaded_at"]).replace(tzinfo=None)
    assert uploaded == datetime.fromisoformat(newer["uploaded_at"]).replace(tzinfo=None)
