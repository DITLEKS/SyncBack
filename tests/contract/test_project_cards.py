"""Карточка проекта (счётчики, цвет, иконка) и состояние анализа в списках документов."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.project_appearance import PROJECT_COLORS
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus
from tests.contract.conftest import create_project, register_and_login, upload_document

pytestmark = pytest.mark.asyncio


async def _add_source(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    *,
    document_id: str | None = None,
) -> None:
    data = {"name": "Регламент"}
    if document_id is not None:
        data |= {"scope": "document", "document_id": document_id}
    response = await client.post(
        f"/api/v1/projects/{project_id}/sources/file",
        data=data,
        files={"file": ("rules.txt", b"rules\n", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def _fail_analysis(sessionmaker: async_sessionmaker[AsyncSession], document_id: str) -> str:
    """Неудачная задача анализа, ставшая текущей для документа (документ остаётся draft)."""
    doc_id = uuid.UUID(document_id)
    async with sessionmaker() as session:
        job = AnalysisJob(
            document_id=doc_id,
            status=AnalysisJobStatus.FAILED,
            error_code="LLM_TIMEOUT",
            error_message="Модель не ответила вовремя",
        )
        session.add(job)
        await session.flush()
        await session.execute(
            update(Document).where(Document.id == doc_id).values(current_analysis_job_id=job.id)
        )
        await session.commit()
        return str(job.id)


async def test_project_cards_count_documents_and_base_sources(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    empty_project = await create_project(client, headers, name="Пустой")
    first = await upload_document(client, headers, project_id)
    await upload_document(client, headers, project_id, filename="second.txt")
    await _add_source(client, headers, project_id)
    # Источник документа — не базовый, в source_count не входит.
    await _add_source(client, headers, project_id, document_id=first["id"])

    response = await client.get("/api/v1/projects", headers=headers)
    assert response.status_code == 200, response.text
    cards = {p["id"]: p for p in response.json()["items"]}
    assert (cards[project_id]["document_count"], cards[project_id]["source_count"]) == (2, 1)
    assert (cards[empty_project]["document_count"], cards[empty_project]["source_count"]) == (0, 0)

    response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert (response.json()["document_count"], response.json()["source_count"]) == (2, 1)


async def test_project_color_is_assigned_from_palette_and_persisted(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    first = await create_project(client, headers)
    second = await create_project(client, headers, name="Второй")

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Свой цвет", "color": "#ec4899", "icon": "📘"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    custom = response.json()
    assert (custom["color"], custom["icon"]) == ("EC4899", "📘")

    response = await client.get("/api/v1/projects", headers=headers)
    colors = {p["id"]: p["color"] for p in response.json()["items"]}
    assert colors[first] == PROJECT_COLORS[0]
    assert colors[second] == PROJECT_COLORS[1]
    assert colors[custom["id"]] == "EC4899"


async def test_project_color_and_icon_can_be_updated_and_reset(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    url = f"/api/v1/projects/{project_id}"

    response = await client.patch(url, json={"color": "10b981", "icon": "folder"}, headers=headers)
    assert response.status_code == 200, response.text
    assert (response.json()["color"], response.json()["icon"]) == ("10B981", "folder")

    response = await client.patch(url, json={"icon": ""}, headers=headers)
    assert response.status_code == 200, response.text
    assert (response.json()["color"], response.json()["icon"]) == ("10B981", None)


@pytest.mark.parametrize("color", ["123456", "zzzzzz"])
async def test_project_color_outside_palette_is_rejected(client: AsyncClient, color: str) -> None:
    headers = await register_and_login(client)
    response = await client.post(
        "/api/v1/projects", json={"name": "Проект", "color": color}, headers=headers
    )
    assert response.status_code == 422, response.text

    project_id = await create_project(client, headers)
    response = await client.patch(
        f"/api/v1/projects/{project_id}", json={"color": color}, headers=headers
    )
    assert response.status_code == 422, response.text


async def test_failed_analysis_is_visible_in_every_document_view(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    failed = await upload_document(client, headers, project_id)
    untouched = await upload_document(client, headers, project_id, filename="new.txt")
    job_id = await _fail_analysis(sessionmaker, failed["id"])
    expected = {
        "job_id": job_id,
        "state": "failed",
        "error_code": "LLM_TIMEOUT",
        "error_message": "Модель не ответила вовремя",
        "can_retry": True,
    }

    def analysis_by_id(items: list[dict]) -> dict[str, dict | None]:
        return {item["id"]: item["analysis"] for item in items}

    response = await client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200, response.text
    mine = analysis_by_id(response.json()["items"])
    assert mine[failed["id"]] == expected
    assert mine[untouched["id"]] is None

    response = await client.get(
        f"/api/v1/projects/{project_id}", params={"include": "documents"}, headers=headers
    )
    assert analysis_by_id(response.json()["documents"])[failed["id"]] == expected

    response = await client.get(f"/api/v1/projects/{project_id}/documents", headers=headers)
    assert analysis_by_id(response.json()["items"])[failed["id"]] == expected

    response = await client.get(
        f"/api/v1/projects/{project_id}/documents/{failed['id']}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"
    assert response.json()["analysis"] == expected
