"""Пайплайн воркера на реальной БД: обработка источников и финализация задачи."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.workers.tasks.analysis_tasks as tasks
from app.domain.events import DomainEvent
from app.domain.exceptions import LLMInputTooLargeError, LLMInvalidResponseError, LLMTimeoutError
from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.domain.interfaces.source_connector import SourceRef
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from tests.contract.conftest import (
    InMemoryFileStorage,
    create_project,
    register_and_login,
    upload_document,
)
from tests.contract.test_analysis_jobs import FakeAnalysisQueue, queue  # noqa: F401

pytestmark = pytest.mark.asyncio


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_for_source_text: str | None = None
        self.failure: Exception = RuntimeError("LLM недоступна")

    async def generate_suggestions(
        self, document_text: str, source_text: str, document_format: str
    ) -> LLMSuggestionBatch:
        self.calls.append((document_text, source_text, document_format))
        if source_text == self.fail_for_source_text:
            raise self.failure
        return LLMSuggestionBatch(
            items=[
                LLMSuggestionItem("p1", "modify", "Hello", f"Hi ({source_text})"),
                LLMSuggestionItem("p2", "unknown", None, None),
            ]
        )


class FakeConnector:
    async def fetch(self, source: SourceRef) -> str:
        return source.name


@pytest.fixture
def published_events(monkeypatch: pytest.MonkeyPatch) -> list[DomainEvent]:
    """Вместо публикации в Redis события воркера собираются в список."""
    events: list[DomainEvent] = []

    class Recorder:
        async def publish(self, event: DomainEvent) -> None:
            events.append(event)

    monkeypatch.setattr(tasks, "_event_publisher", Recorder)
    return events


@pytest.fixture
def worker(
    monkeypatch: pytest.MonkeyPatch,
    sessionmaker: async_sessionmaker[AsyncSession],
    file_storage: InMemoryFileStorage,
    published_events: list[DomainEvent],
) -> FakeLLM:
    """Воркер работает с той же SQLite-БД, что и API, без Celery, MinIO и Redis."""

    @asynccontextmanager
    async def uow_factory() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with sessionmaker() as session:
            yield SqlAlchemyUnitOfWork(session)

    @asynccontextmanager
    async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
        yield redis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    llm = FakeLLM()
    monkeypatch.setattr(tasks, "uow_factory", uow_factory)
    monkeypatch.setattr(tasks, "_redis", fake_redis)
    monkeypatch.setattr(tasks, "_get_storage", lambda: file_storage)
    monkeypatch.setattr(tasks, "_get_llm_client", lambda: llm)
    monkeypatch.setattr(tasks, "_get_connector_for", lambda kind: FakeConnector())
    return llm


async def _start(
    client: AsyncClient, queue: FakeAnalysisQueue, source_names: list[str]
) -> tuple[dict[str, str], str, str, list[str]]:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    document = await upload_document(client, headers, project_id, content=b"Hello world\n")
    for name in source_names:
        response = await client.post(
            f"/api/v1/projects/{project_id}/sources/note",
            json={"name": name, "text_content": "t", "scope": "project"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs",
        headers=headers,
    )
    assert response.status_code == 201, response.text
    job_id, source_ids = queue.enqueued[-1]
    return headers, document["id"], str(job_id), [str(s) for s in source_ids]


async def _document(sessionmaker: async_sessionmaker[AsyncSession], document_id: str) -> Document:
    async with sessionmaker() as session:
        doc = await session.get(Document, uuid.UUID(document_id))
        assert doc is not None
        return doc


async def _job(sessionmaker: async_sessionmaker[AsyncSession], job_id: str) -> AnalysisJob:
    async with sessionmaker() as session:
        job = await session.get(AnalysisJob, uuid.UUID(job_id))
        assert job is not None
        return job


async def test_pipeline_success_moves_document_to_awaiting_approval(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    sessionmaker: async_sessionmaker[AsyncSession],
    published_events: list[DomainEvent],
) -> None:
    headers, document_id, job_id, source_ids = await _start(client, queue, ["A", "B"])
    assert len(source_ids) == 2

    assert (await _job(sessionmaker, job_id)).status.value == "dispatched"
    results = [await tasks._process_source(job_id, sid) for sid in source_ids]
    assert [r["status"] for r in results] == ["ok", "ok"]
    assert [r["count"] for r in results] == [1, 1]
    # Первый обработанный источник переводит задачу в processing
    job = await _job(sessionmaker, job_id)
    assert job.status.value == "processing"
    assert job.started_at is not None
    # Документ парсится один раз, дальше текст берётся из кэша
    assert {call[0] for call in worker.calls} == {"Hello world\n"}
    assert {call[2] for call in worker.calls} == {"txt"}

    await tasks._finalize(results, job_id)

    job = await _job(sessionmaker, job_id)
    assert job.status.value == "success"
    assert job.finished_at is not None
    assert job.partial_success is False
    assert (await _document(sessionmaker, document_id)).status.value == "awaiting_approval"
    assert [(str(e.document_id), e.status.value) for e in published_events] == [
        (document_id, "awaiting_approval")
    ]

    async with sessionmaker() as session:
        suggestions = (await session.execute(select(Suggestion))).scalars().all()
    assert len(suggestions) == 2
    assert {s.suggested_text for s in suggestions} == {"Hi (A)", "Hi (B)"}
    assert all(str(s.document_id) == document_id for s in suggestions)
    assert all(s.status.value == "pending" for s in suggestions)

    # Правки видны через API
    response = await client.get(
        f"/api/v1/projects/{(await _document(sessionmaker, document_id)).project_id}"
        f"/documents/{document_id}/suggestions",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2


async def test_pipeline_partial_failure(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    worker.fail_for_source_text = "B"
    _, document_id, job_id, source_ids = await _start(client, queue, ["A", "B"])

    results = [await tasks._process_source(job_id, sid) for sid in source_ids]
    assert sorted(r["status"] for r in results) == ["failed", "ok"]
    failed = next(r for r in results if r["status"] == "failed")
    assert failed["error_code"] == "GENERATION_ERROR"

    await tasks._finalize(results, job_id)
    job = await _job(sessionmaker, job_id)
    assert job.status.value == "partial_success"
    assert job.partial_success is True
    assert job.error_code == "PARTIAL_FAILURE"
    assert "LLM недоступна" in (job.error_message or "")
    assert (await _document(sessionmaker, document_id)).status.value == "awaiting_approval"


async def test_pipeline_all_failed_returns_document_to_draft(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    worker.fail_for_source_text = "A"
    _, document_id, job_id, source_ids = await _start(client, queue, ["A"])

    results = [await tasks._process_source(job_id, sid) for sid in source_ids]
    await tasks._finalize(results, job_id)

    job = await _job(sessionmaker, job_id)
    assert job.status.value == "failed"
    assert job.error_code == "GENERATION_ERROR"
    assert (await _document(sessionmaker, document_id)).status.value == "draft"


async def test_pipeline_without_sources_marks_document_ready(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, document_id, job_id, source_ids = await _start(client, queue, [])
    assert source_ids == []
    await tasks._finalize([], job_id)
    assert (await _job(sessionmaker, job_id)).status.value == "success"
    assert (await _document(sessionmaker, document_id)).status.value == "ready"


async def test_cancelled_job_is_not_overwritten_by_finalize(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    headers, document_id, job_id, source_ids = await _start(client, queue, ["A"])
    project_id = (await _document(sessionmaker, document_id)).project_id

    response = await client.delete(
        f"/api/v1/projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    results = [await tasks._process_source(job_id, sid) for sid in source_ids]
    assert results[0]["status"] == "cancelled"
    await tasks._finalize(results, job_id)

    assert (await _job(sessionmaker, job_id)).status.value == "cancelled"
    assert (await _document(sessionmaker, document_id)).status.value == "draft"
    async with sessionmaker() as session:
        assert (await session.execute(select(Suggestion))).scalars().all() == []


async def test_source_not_found_is_reported(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
) -> None:
    _, _, job_id, _ = await _start(client, queue, [])
    result = await tasks._process_source(job_id, str(uuid.uuid4()))
    assert result["status"] == "failed"
    assert result["error_code"] == "SOURCE_NOT_FOUND"
    assert (await tasks._process_source(str(uuid.uuid4()), str(uuid.uuid4())))["error_code"] == (
        "JOB_NOT_FOUND"
    )


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (LLMInputTooLargeError("слишком длинно"), "LLM_INPUT_TOO_LARGE"),
        (LLMTimeoutError("нет ответа"), "LLM_UNAVAILABLE"),
        (LLMInvalidResponseError("HTTP 400"), "LLM_INVALID_RESPONSE"),
    ],
)
async def test_llm_failures_are_reported_with_specific_codes(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
    worker: FakeLLM,
    failure: Exception,
    error_code: str,
) -> None:
    worker.fail_for_source_text = "A"
    worker.failure = failure
    _, _, job_id, source_ids = await _start(client, queue, ["A"])

    result = await tasks._process_source(job_id, source_ids[0])

    assert result["status"] == "failed"
    assert result["error_code"] == error_code
