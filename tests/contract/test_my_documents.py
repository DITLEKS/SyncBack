"""GET /api/v1/documents — «Мои документы»: документы всех проектов пользователя
со счётчиками правок текущего анализа, фильтрами, поиском и пагинацией."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import (
    AnalysisJobStatus,
    ChangeType,
    DocumentStatus,
    SuggestionStatus,
)
from app.infrastructure.db.models.suggestion import Suggestion
from tests.contract.conftest import create_project, register_and_login, upload_document

pytestmark = pytest.mark.asyncio

URL = "/api/v1/documents"


async def _add_analysis(
    sessionmaker: async_sessionmaker[AsyncSession],
    document_id: str,
    statuses: list[SuggestionStatus],
    *,
    current: bool,
) -> None:
    """Завершённый анализ с правками в заданных статусах; current — сделать его
    текущим для документа и перевести документ в awaiting_approval."""
    doc_id = uuid.UUID(document_id)
    async with sessionmaker() as session:
        job = AnalysisJob(document_id=doc_id, status=AnalysisJobStatus.SUCCESS)
        session.add(job)
        await session.flush()
        session.add_all(
            Suggestion(
                analysis_job_id=job.id,
                document_id=doc_id,
                section_ref=f"p{i}",
                change_type=ChangeType.MODIFY,
                status=status,
                original_text="old",
                suggested_text="new",
            )
            for i, status in enumerate(statuses)
        )
        if current:
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status=DocumentStatus.AWAITING_APPROVAL, current_analysis_job_id=job.id)
            )
        await session.commit()


async def test_list_my_documents_counts_only_current_analysis(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await register_and_login(client)
    first_project = await create_project(client, headers)
    second_project = await create_project(client, headers)
    reviewed = await upload_document(client, headers, first_project, filename="report.txt")
    plain = await upload_document(client, headers, second_project, filename="notes_v2.txt")

    # Правки устаревшего анализа не должны попадать в счётчики.
    await _add_analysis(sessionmaker, reviewed["id"], [SuggestionStatus.PENDING] * 5, current=False)
    await _add_analysis(
        sessionmaker,
        reviewed["id"],
        [SuggestionStatus.PENDING, SuggestionStatus.ACCEPTED, SuggestionStatus.REJECTED],
        current=True,
    )

    response = await client.get(URL, headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2
    assert sorted(item["name"] for item in page["items"]) == ["notes_v2.txt", "report.txt"]

    by_name = {item["name"]: item for item in page["items"]}
    assert by_name["report.txt"]["status"] == "awaiting_approval"
    assert by_name["report.txt"]["suggestions"] == {
        "total": 3,
        "pending": 1,
        "accepted": 1,
        "rejected": 1,
    }
    assert by_name["report.txt"]["project"]["id"] == first_project
    assert by_name["notes_v2.txt"]["suggestions"] == {
        "total": 0,
        "pending": 0,
        "accepted": 0,
        "rejected": 0,
    }
    assert by_name["notes_v2.txt"]["project"]["id"] == second_project
    assert plain["id"] == by_name["notes_v2.txt"]["id"]


async def test_list_my_documents_filters_search_sort_and_pagination(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    names = ["alpha.txt", "beta_report.txt", "gamma.txt"]
    docs = {
        name: await upload_document(client, headers, project_id, filename=name) for name in names
    }
    await _add_analysis(
        sessionmaker, docs["beta_report.txt"]["id"], [SuggestionStatus.PENDING], current=True
    )
    await _add_analysis(
        sessionmaker, docs["gamma.txt"]["id"], [SuggestionStatus.ACCEPTED], current=True
    )

    async def names_for(query: str) -> list[str]:
        response = await client.get(f"{URL}?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return [item["name"] for item in response.json()["items"]]

    assert await names_for("status=draft") == ["alpha.txt"]
    assert sorted(await names_for("status=awaiting_approval")) == ["beta_report.txt", "gamma.txt"]
    assert await names_for("outdated=true") == ["beta_report.txt"]
    assert await names_for("search=REPORT") == ["beta_report.txt"]
    # «_» в поиске — обычный символ, а не шаблон LIKE.
    assert await names_for("search=a_r") == ["beta_report.txt"]
    assert await names_for("sort_by=name&sort_dir=asc") == names
    assert await names_for("sort_by=name&sort_dir=desc") == list(reversed(names))

    response = await client.get(
        f"{URL}?sort_by=name&sort_dir=asc&limit=2&offset=1", headers=headers
    )
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert [item["name"] for item in page["items"]] == ["beta_report.txt", "gamma.txt"]

    response = await client.get(f"{URL}?offset=10", headers=headers)
    assert response.json() == {"items": [], "total": 3, "limit": 50, "offset": 10}

    response = await client.get(f"{URL}?status=ready&offset=10", headers=headers)
    assert response.json()["total"] == 0


async def test_list_my_documents_is_scoped_to_owner(client: AsyncClient) -> None:
    owner = await register_and_login(client, "owner@example.com")
    project_id = await create_project(client, owner)
    await upload_document(client, owner, project_id)

    stranger = await register_and_login(client, "stranger@example.com")
    response = await client.get(URL, headers=stranger)
    assert response.status_code == 200
    assert response.json()["total"] == 0

    assert (await client.get(URL)).status_code == 401
