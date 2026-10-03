"""Задачи анализа: запуск, идемпотентность, force для READY, отмена, очередь недоступна."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.dependencies as dependencies
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus
from tests.contract.conftest import create_project, register_and_login, upload_document

pytestmark = pytest.mark.asyncio


class FakeAnalysisQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[uuid.UUID, list[uuid.UUID]]] = []
        self.revoked: list[str] = []
        self.fail = False

    async def enqueue(self, job_id: uuid.UUID, source_ids: Sequence[uuid.UUID]) -> str:
        if self.fail:
            raise ConnectionError("broker down")
        self.enqueued.append((job_id, list(source_ids)))
        return f"task-{len(self.enqueued)}"

    async def revoke(self, task_id: str) -> None:
        self.revoked.append(task_id)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeAnalysisQueue:
    fake = FakeAnalysisQueue()
    monkeypatch.setattr(dependencies, "_get_analysis_queue", lambda: fake)
    return fake


async def _document_state(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: str
) -> tuple[str, uuid.UUID | None]:
    async with sessionmaker() as session:
        doc = await session.get(Document, uuid.UUID(document_id))
        assert doc is not None
        return doc.status.value, doc.current_analysis_job_id


async def _job_row(sessionmaker: async_sessionmaker[AsyncSession], job_id: str) -> AnalysisJob:
    async with sessionmaker() as session:
        job = await session.get(AnalysisJob, uuid.UUID(job_id))
        assert job is not None
        return job


async def test_start_job_dispatches_project_and_document_sources(
    client: AsyncClient,
    queue: FakeAnalysisQueue,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id)
    other_document = await upload_document(client, headers, project_id, filename="other.txt")

    async def add_note(name: str, scope: str, document_id: str | None = None) -> str:
        body: dict = {"name": name, "text_content": "текст", "scope": scope}
        if document_id:
            body["document_id"] = document_id
        response = await client.post(
            f"/api/v1/projects/{project_id}/sources/note", json=body, headers=headers
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    base_source = await add_note("Базовый", "project")
    own_source = await add_note("Свой", "document", document["id"])
    await add_note("Чужой", "document", other_document["id"])

    url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"
    response = await client.post(url, headers=headers)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["status"] == "processing"
    assert job["document_id"] == document["id"]

    assert len(queue.enqueued) == 1
    job_id, source_ids = queue.enqueued[0]
    assert str(job_id) == job["id"]
    assert set(source_ids) == {uuid.UUID(base_source), uuid.UUID(own_source)}

    assert await _document_state(sessionmaker, document["id"]) == ("in_progress", job_id)
    row = await _job_row(sessionmaker, job["id"])
    assert row.celery_task_id == "task-1"
    assert row.started_at is not None

    # Пока анализ идёт, второй запуск невозможен
    response = await client.post(url, headers=headers)
    assert response.status_code == 409

    response = await client.get(f"{url}/{job['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processing"


async def test_idempotency_key_returns_existing_job(
    client: AsyncClient, queue: FakeAnalysisQueue
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id)
    url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"

    first = await client.post(url, headers={**headers, "Idempotency-Key": "key-1"})
    assert first.status_code == 201, first.text
    second = await client.post(url, headers={**headers, "Idempotency-Key": "key-1"})
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(queue.enqueued) == 1


async def test_ready_document_requires_force(
    client: AsyncClient,
    queue: FakeAnalysisQueue,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id)
    async with sessionmaker() as session:
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(document["id"]))
            .values(status=DocumentStatus.READY)
        )
        await session.commit()

    url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"
    response = await client.post(url, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["confirmation_required"] is True
    assert queue.enqueued == []

    response = await client.post(url, json={"force": True}, headers=headers)
    assert response.status_code == 201, response.text
    assert (await _document_state(sessionmaker, document["id"]))[0] == "in_progress"


async def test_cancel_job_returns_document_to_draft(
    client: AsyncClient,
    queue: FakeAnalysisQueue,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id)
    url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"
    job = (await client.post(url, headers=headers)).json()

    response = await client.delete(f"{url}/{job['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert queue.revoked == ["task-1"]
    assert (await _document_state(sessionmaker, document["id"]))[0] == "draft"
    assert (await _job_row(sessionmaker, job["id"])).finished_at is not None

    # Повторная отмена идемпотентна, отмена чужого job — 404
    assert (await client.delete(f"{url}/{job['id']}", headers=headers)).status_code == 200
    assert (await client.delete(f"{url}/{uuid.uuid4()}", headers=headers)).status_code == 404

    # После отмены можно запустить заново
    response = await client.post(url, headers=headers)
    assert response.status_code == 201, response.text
    async with sessionmaker() as session:
        jobs = (await session.execute(select(AnalysisJob))).scalars().all()
    assert len(jobs) == 2


async def test_queue_unavailable_marks_job_failed(
    client: AsyncClient,
    queue: FakeAnalysisQueue,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id)
    queue.fail = True

    url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"
    response = await client.post(url, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "QUEUE_UNAVAILABLE"
    assert (await _document_state(sessionmaker, document["id"]))[0] == "draft"
