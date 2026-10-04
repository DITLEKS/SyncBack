"""Правки документа: список с фильтром, PATCH (single/bulk/reset), ревью, аудит."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.audit_log import AuditLog
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


@dataclass
class ReviewFixture:
    headers: dict[str, str]
    project_id: str
    document_id: str
    job_id: uuid.UUID
    suggestion_ids: list[uuid.UUID]
    stale_suggestion_id: uuid.UUID

    @property
    def base_url(self) -> str:
        return f"/api/v1/projects/{self.project_id}/documents/{self.document_id}/suggestions"


@pytest_asyncio.fixture
async def review(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> ReviewFixture:
    """Документ в awaiting_approval с завершённым анализом, тремя правками
    и одной правкой устаревшего анализа."""
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id, content=b"Alpha\nBeta\nGamma\n")
    document_id = uuid.UUID(document["id"])

    def make_suggestion(job_id: uuid.UUID, index: int) -> Suggestion:
        return Suggestion(
            analysis_job_id=job_id,
            document_id=document_id,
            section_ref=f"p{index}",
            change_type=ChangeType.MODIFY,
            status=SuggestionStatus.PENDING,
            original_text=f"old {index}",
            suggested_text=f"new {index}",
        )

    async with sessionmaker() as session:
        stale_job = AnalysisJob(document_id=document_id, status=AnalysisJobStatus.SUCCESS)
        current_job = AnalysisJob(document_id=document_id, status=AnalysisJobStatus.SUCCESS)
        session.add_all([stale_job, current_job])
        await session.flush()
        stale = make_suggestion(stale_job.id, 0)
        current = [make_suggestion(current_job.id, i) for i in range(1, 4)]
        session.add_all([stale, *current])
        await session.flush()
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                status=DocumentStatus.AWAITING_APPROVAL,
                current_analysis_job_id=current_job.id,
            )
        )
        await session.commit()
        return ReviewFixture(
            headers=headers,
            project_id=project_id,
            document_id=str(document_id),
            job_id=current_job.id,
            suggestion_ids=[s.id for s in current],
            stale_suggestion_id=stale.id,
        )


async def _statuses(
    sessionmaker: async_sessionmaker[AsyncSession], ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    async with sessionmaker() as session:
        rows = (await session.execute(select(Suggestion).where(Suggestion.id.in_(ids)))).scalars()
        return {s.id: s.status.value for s in rows}


async def _document_status(sessionmaker: async_sessionmaker[AsyncSession], document_id: str) -> str:
    async with sessionmaker() as session:
        document = await session.get(Document, uuid.UUID(document_id))
        assert document is not None
        return document.status.value


async def _audit_actions(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[tuple[uuid.UUID | None, str]]:
    async with sessionmaker() as session:
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars()
        return [(e.suggestion_id, e.action) for e in rows]


async def test_list_suggestions_with_status_filter(
    client: AsyncClient, review: ReviewFixture
) -> None:
    response = await client.get(review.base_url, headers=review.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert {s["id"] for s in body["items"]} == {str(i) for i in review.suggestion_ids}
    assert {s["original_text"] for s in body["items"]} == {"old 1", "old 2", "old 3"}

    response = await client.get(
        review.base_url, params={"status": "accepted"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0

    response = await client.get(review.base_url, params={"status": "weird"}, headers=review.headers)
    assert response.status_code == 400


async def test_suggestion_fields_for_editor(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Редактору нужны тип правки, обоснование и позиция; у add нет исходного текста."""
    async with sessionmaker() as session:
        added = Suggestion(
            analysis_job_id=review.job_id,
            document_id=uuid.UUID(review.document_id),
            section_ref="p9",
            change_type=ChangeType.ADD,
            status=SuggestionStatus.PENDING,
            original_text=None,
            suggested_text="Новый абзац",
            rationale="В источнике появился раздел",
            confidence_score=0.9,
            block_id="b9",
            start_offset=12,
            end_offset=12,
        )
        session.add(added)
        await session.commit()

    response = await client.get(f"{review.base_url}/{added.id}", headers=review.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {
        k: body[k]
        for k in (
            "analysis_job_id",
            "change_type",
            "original_text",
            "suggested_text",
            "rationale",
            "confidence_score",
            "block_id",
            "start_offset",
            "end_offset",
            "status",
        )
    } == {
        "analysis_job_id": str(review.job_id),
        "change_type": "add",
        "original_text": None,
        "suggested_text": "Новый абзац",
        "rationale": "В источнике появился раздел",
        "confidence_score": 0.9,
        "block_id": "b9",
        "start_offset": 12,
        "end_offset": 12,
        "status": "pending",
    }
    assert "comment" not in body

    response = await client.get(review.base_url, headers=review.headers)
    assert response.status_code == 200, response.text
    assert {i["change_type"] for i in response.json()["items"]} == {"add", "modify"}


async def test_get_single_suggestion_and_stale(client: AsyncClient, review: ReviewFixture) -> None:
    response = await client.get(
        f"{review.base_url}/{review.suggestion_ids[0]}", headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"

    response = await client.get(
        f"{review.base_url}/{review.stale_suggestion_id}", headers=review.headers
    )
    assert response.status_code == 404


async def test_patch_by_ids_then_reset(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first, second, third = review.suggestion_ids

    response = await client.patch(
        review.base_url,
        json={"ids": [str(first), str(second)], "status": "accepted"},
        headers=review.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 2
    assert response.json()["document_status"] == "awaiting_approval"
    statuses = await _statuses(sessionmaker, review.suggestion_ids)
    assert statuses == {first: "accepted", second: "accepted", third: "pending"}

    # Повторное решение по уже принятой правке — конфликт, ничего не меняется
    response = await client.patch(
        review.base_url,
        json={"ids": [str(first), str(third)], "status": "rejected"},
        headers=review.headers,
    )
    assert response.status_code == 409
    assert await _statuses(sessionmaker, review.suggestion_ids) == statuses

    # Правка устаревшего анализа не принимается
    response = await client.patch(
        review.base_url,
        json={"ids": [str(review.stale_suggestion_id)], "status": "accepted"},
        headers=review.headers,
    )
    assert response.status_code == 409

    response = await client.patch(
        review.base_url, json={"ids": [str(first)], "status": "pending"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 1
    assert (await _statuses(sessionmaker, [first]))[first] == "pending"

    response = await client.patch(
        review.base_url, json={"ids": [str(first)], "status": "pending"}, headers=review.headers
    )
    assert response.status_code == 409

    assert await _audit_actions(sessionmaker) == [
        (first, "accept"),
        (second, "accept"),
        (first, "reset"),
    ]


async def test_patch_by_filter(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    response = await client.patch(
        review.base_url, json={"filter": "pending", "status": "rejected"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 3
    assert set((await _statuses(sessionmaker, review.suggestion_ids)).values()) == {"rejected"}
    assert (await _statuses(sessionmaker, [review.stale_suggestion_id])) == {
        review.stale_suggestion_id: "pending"
    }

    response = await client.patch(
        review.base_url, json={"filter": "decided", "status": "accepted"}, headers=review.headers
    )
    assert response.status_code == 409

    response = await client.patch(
        review.base_url, json={"filter": "decided", "status": "pending"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 3
    assert set((await _statuses(sessionmaker, review.suggestion_ids)).values()) == {"pending"}

    response = await client.patch(
        review.base_url, json={"status": "accepted"}, headers=review.headers
    )
    assert response.status_code == 422


async def test_review_save_with_if_match_and_finalize(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first, second, third = review.suggestion_ids
    url = f"{review.base_url}/review"

    response = await client.put(
        url,
        json={
            "review_version": 0,
            "decisions": [{"suggestion_id": str(first), "decision": "accepted"}],
            "finalize": False,
        },
        headers={**review.headers, "If-Match": '"0"'},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_version"] == 1
    assert body["accepted_count"] == 1
    assert body["pending_count"] == 2
    assert body["finalized"] is False

    # Устаревшая версия
    response = await client.put(
        url,
        json={"review_version": 0, "decisions": [], "finalize": False},
        headers={**review.headers, "If-Match": '"0"'},
    )
    assert response.status_code == 412
    response = await client.put(
        url, json={"review_version": 0, "decisions": [], "finalize": False}, headers=review.headers
    )
    assert response.status_code == 409

    # Финализация при оставшихся pending невозможна
    response = await client.put(
        url, json={"review_version": 1, "decisions": [], "finalize": True}, headers=review.headers
    )
    assert response.status_code == 409
    statuses = await _statuses(sessionmaker, review.suggestion_ids)
    assert statuses == {first: "accepted", second: "pending", third: "pending"}

    response = await client.put(
        url,
        json={
            "review_version": 1,
            "decisions": [
                {"suggestion_id": str(second), "decision": "rejected"},
                {"suggestion_id": str(third), "decision": "accepted"},
            ],
            "finalize": True,
        },
        headers=review.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finalized"] is True
    assert body["document_status"] == "ready"
    assert body["pending_count"] == 0

    assert await _audit_actions(sessionmaker) == [
        (first, "accept"),
        (second, "reject"),
        (third, "accept"),
    ]

    # В готовом документе принять или отклонить нельзя, только отменить решение
    response = await client.patch(
        review.base_url, json={"ids": [str(first)], "status": "rejected"}, headers=review.headers
    )
    assert response.status_code == 409


async def _finalize_all(client: AsyncClient, review: ReviewFixture) -> None:
    response = await client.patch(
        review.base_url, json={"filter": "pending", "status": "accepted"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    response = await client.put(
        f"{review.base_url}/review",
        json={"review_version": 0, "decisions": [], "finalize": True},
        headers=review.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["document_status"] == "ready"


async def test_reset_one_decision_reopens_ready_document(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await _finalize_all(client, review)
    first, second, _ = review.suggestion_ids

    response = await client.post(f"{review.base_url}/{first}/reset", headers=review.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert (await _document_status(sessionmaker, review.document_id)) == "awaiting_approval"
    assert await _statuses(sessionmaker, review.suggestion_ids) == {
        first: "pending",
        second: "accepted",
        review.suggestion_ids[2]: "accepted",
    }

    # Решение снова можно принять и завершить ревью
    response = await client.put(
        f"{review.base_url}/review",
        json={
            "review_version": 1,
            "decisions": [{"suggestion_id": str(first), "decision": "rejected"}],
            "finalize": True,
        },
        headers=review.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["document_status"] == "ready"


async def test_patch_reset_in_ready_document(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await _finalize_all(client, review)
    first, second, third = review.suggestion_ids

    # Сбрасывать нечего — документ остаётся готовым
    response = await client.patch(
        review.base_url, json={"filter": "pending", "status": "pending"}, headers=review.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated_count"] == 0
    assert response.json()["document_status"] == "ready"

    response = await client.patch(
        review.base_url,
        json={"ids": [str(first), str(second)], "status": "pending"},
        headers=review.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated_count"] == 2
    assert body["document_status"] == "awaiting_approval"
    assert await _statuses(sessionmaker, review.suggestion_ids) == {
        first: "pending",
        second: "pending",
        third: "accepted",
    }


async def test_reopen_loses_to_concurrent_reanalysis(
    client: AsyncClient,
    review: ReviewFixture,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Документ ушёл на повторный анализ, пока шла отмена решения: 409 и ничего не меняется."""
    await _finalize_all(client, review)
    first = review.suggestion_ids[0]
    original = SuggestionService._reopen_review

    async def reanalysis_wins(self: SuggestionService, document: Document) -> None:
        async with sessionmaker() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document.id)
                .values(status=DocumentStatus.DRAFT)
            )
            await session.commit()
        await original(self, document)

    monkeypatch.setattr(SuggestionService, "_reopen_review", reanalysis_wins)
    response = await client.post(f"{review.base_url}/{first}/reset", headers=review.headers)
    assert response.status_code == 409, response.text
    assert (await _document_status(sessionmaker, review.document_id)) == "draft"
    assert (await _statuses(sessionmaker, [first]))[first] == "accepted"


async def test_reset_endpoint(
    client: AsyncClient, review: ReviewFixture, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first = review.suggestion_ids[0]
    response = await client.post(f"{review.base_url}/{first}/reset", headers=review.headers)
    assert response.status_code == 409  # правка ещё pending

    await client.patch(
        review.base_url, json={"ids": [str(first)], "status": "accepted"}, headers=review.headers
    )
    response = await client.post(f"{review.base_url}/{first}/reset", headers=review.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert response.json()["decided_by"] is None
    assert await _audit_actions(sessionmaker) == [(first, "accept"), (first, "reset")]


async def test_suggestions_of_foreign_project_look_missing(
    client: AsyncClient, review: ReviewFixture
) -> None:
    # Чужой проект неотличим от несуществующего: 404, а не 403
    other_headers = await register_and_login(client, email="other@example.com")
    response = await client.get(review.base_url, headers=other_headers)
    assert response.status_code == 404
    response = await client.patch(
        review.base_url,
        json={"ids": [str(review.suggestion_ids[0])], "status": "accepted"},
        headers=other_headers,
    )
    assert response.status_code == 404
