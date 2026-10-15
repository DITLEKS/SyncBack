"""Два конкурентных воркера на уровне _process_source (PostgreSQL).

Реальные S3, Redis и LLM не используются: загрузка текста документа, коннектор
источника и LLM-клиент подменяются заглушками, UoW строится над pg_sessionmaker.
LLM-заглушка синхронизирована через asyncio.Event, поэтому второй воркер гарантированно
пытается выполнить claim(), пока первый держит статус processing внутри «вызова LLM».
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.exceptions import LLMTimeoutError
from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.analysis_job_source import (
    SOURCE_RUN_COMPLETED,
    SOURCE_RUN_PENDING,
    SOURCE_RUN_PROCESSING,
    AnalysisJobSource,
)
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat, SourceType
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.models.user import User
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.workers.tasks import analysis_tasks

DOCUMENT_TEXT = "Первый абзац документа.\n\nВторой абзац документа."
SOURCE_TEXT = "Текст источника."
# Защита от зависания теста, если синхронизация воркеров сломается.
WAIT_SECONDS = 10


@dataclass
class Scenario:
    job_id: uuid.UUID
    source_id: uuid.UUID


def _batch() -> LLMSuggestionBatch:
    # Две правки modify на разные абзацы; для txt mapper сохраняет обе без изменений.
    return LLMSuggestionBatch(
        items=[
            LLMSuggestionItem(
                section_ref="s1",
                change_type="modify",
                old_text="Первый абзац документа.",
                new_text="Первый абзац обновлён.",
            ),
            LLMSuggestionItem(
                section_ref="s2",
                change_type="modify",
                old_text="Второй абзац документа.",
                new_text="Второй абзац обновлён.",
            ),
        ]
    )


class FakeLLM:
    """Заглушка LLM с ручной синхронизацией.

    started взводится при входе в generate_suggestions, gate удерживает вызов до тех пор,
    пока тест его не отпустит; errors — исключения, которые бросаются на первых вызовах.
    """

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        errors: tuple[Exception, ...] = (),
    ) -> None:
        self.gate = gate
        self.started = started
        self.errors = list(errors)
        self.calls = 0

    async def generate_suggestions(
        self, document_text: str, source_text: str, document_format: str
    ) -> LLMSuggestionBatch:
        self.calls += 1
        assert document_text == DOCUMENT_TEXT
        assert source_text == SOURCE_TEXT
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.errors:
            raise self.errors.pop(0)
        return _batch()


class SequencedLLM:
    """Заглушка LLM, у которой каждый вызов ждёт свой gate: воркеры отпускаются по одному."""

    def __init__(self, calls: int) -> None:
        self.started = [asyncio.Event() for _ in range(calls)]
        self.gates = [asyncio.Event() for _ in range(calls)]
        self.calls = 0

    async def generate_suggestions(
        self, document_text: str, source_text: str, document_format: str
    ) -> LLMSuggestionBatch:
        index = self.calls
        self.calls += 1
        self.started[index].set()
        await self.gates[index].wait()
        return _batch()


class FakeConnector:
    async def fetch(self, source_ref) -> str:
        return SOURCE_TEXT


@pytest_asyncio.fixture
async def scenario(pg_sessionmaker: async_sessionmaker[AsyncSession]) -> Scenario:
    async with pg_sessionmaker() as session:
        user = User(email=f"it-{uuid.uuid4().hex}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(name="process-source", owner_id=user.id)
        session.add(project)
        await session.flush()
        document = Document(
            project_id=project.id,
            name="doc.txt",
            format=DocumentFormat.TXT,
            storage_key=f"it/{uuid.uuid4()}.txt",
            size_bytes=len(DOCUMENT_TEXT.encode()),
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(document)
        await session.flush()
        source = Source(
            project_id=project.id,
            name="source.txt",
            type=SourceType.FILE,
            storage_key=f"it/{uuid.uuid4()}.source.txt",
        )
        session.add(source)
        job = AnalysisJob(document_id=document.id)
        session.add(job)
        await session.commit()
        return Scenario(job_id=job.id, source_id=source.id)


@pytest.fixture
def worker_env(
    monkeypatch: pytest.MonkeyPatch, pg_sessionmaker: async_sessionmaker[AsyncSession]
) -> Callable[[FakeLLM | SequencedLLM], None]:
    """Подменяет инфраструктуру analysis_tasks: БД через pg_sessionmaker, без S3/Redis/LLM."""

    @asynccontextmanager
    async def _uow() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with pg_sessionmaker() as session:
            yield SqlAlchemyUnitOfWork(session)

    async def _load_document_text(job_id: uuid.UUID, storage_key: str) -> str:
        return DOCUMENT_TEXT

    monkeypatch.setattr(analysis_tasks, "uow_factory", _uow)
    monkeypatch.setattr(analysis_tasks, "_load_document_text", _load_document_text)
    monkeypatch.setattr(analysis_tasks, "_get_connector_for", lambda source_kind: FakeConnector())

    def install(llm: FakeLLM | SequencedLLM) -> None:
        monkeypatch.setattr(analysis_tasks, "_get_llm_client", lambda: llm)

    return install


async def _run(scenario: Scenario) -> dict:
    return await analysis_tasks._process_source(str(scenario.job_id), str(scenario.source_id))


async def _row(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> AnalysisJobSource:
    async with sessionmaker() as session:
        row = (
            await session.execute(
                select(AnalysisJobSource).where(
                    AnalysisJobSource.job_id == scenario.job_id,
                    AnalysisJobSource.source_id == scenario.source_id,
                )
            )
        ).scalar_one()
        return row


async def _suggestions_count(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> int:
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Suggestion)
                .where(Suggestion.analysis_job_id == scenario.job_id)
            )
        ).scalar_one()


async def test_two_workers_call_llm_once(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
    scenario: Scenario,
    worker_env: Callable[[FakeLLM | SequencedLLM], None],
) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    llm = FakeLLM(gate=gate, started=started)
    worker_env(llm)
    source_id = str(scenario.source_id)

    # Первый воркер захватывает источник и «висит» внутри вызова LLM.
    first = asyncio.create_task(_run(scenario))
    await asyncio.wait_for(started.wait(), WAIT_SECONDS)
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.locked_at is not None

    # Второй воркер в это время проигрывает claim: без LLM, без записи правок.
    second = await asyncio.wait_for(_run(scenario), WAIT_SECONDS)
    assert second == {"source_id": source_id, "status": "busy"}
    assert llm.calls == 1
    assert await _suggestions_count(pg_sessionmaker, scenario) == 0

    # Первый завершает обработку и сохраняет правки.
    gate.set()
    first_result = await asyncio.wait_for(first, WAIT_SECONDS)
    assert first_result == {"source_id": source_id, "status": "ok", "count": 2}

    # Повтор второго воркера после завершения первого: уже сохранённый ok без LLM.
    retried = await _run(scenario)
    assert retried == {"source_id": source_id, "status": "ok", "count": 2}
    assert llm.calls == 1

    assert await _suggestions_count(pg_sessionmaker, scenario) == 2
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.suggestions_count == 2
    assert row.locked_at is None


async def test_llm_timeout_releases_source_for_immediate_retry(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
    scenario: Scenario,
    worker_env: Callable[[FakeLLM | SequencedLLM], None],
) -> None:
    llm = FakeLLM(errors=(LLMTimeoutError("LLM не ответил вовремя"),))
    worker_env(llm)
    source_id = str(scenario.source_id)

    first = await _run(scenario)
    assert first["status"] == "retryable"
    assert first["error_code"] == "LLM_UNAVAILABLE"
    assert llm.calls == 1

    # release() вернул источник в pending без ожидания lease.
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_PENDING
    assert row.locked_at is None
    assert await _suggestions_count(pg_sessionmaker, scenario) == 0

    # Немедленный повтор снова захватывает источник и доводит обработку до конца.
    second = await _run(scenario)
    assert second == {"source_id": source_id, "status": "ok", "count": 2}
    assert llm.calls == 2
    assert await _suggestions_count(pg_sessionmaker, scenario) == 2
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.locked_at is None


async def _expire_lease(sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == scenario.job_id,
                AnalysisJobSource.source_id == scenario.source_id,
            )
            .values(
                locked_at=datetime.now(timezone.utc)
                - timedelta(seconds=analysis_tasks._SOURCE_LEASE_SECONDS + 60)
            )
        )
        await session.commit()


async def test_worker_with_expired_lease_does_not_store_result(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
    scenario: Scenario,
    worker_env: Callable[[FakeLLM | SequencedLLM], None],
) -> None:
    """A завис в LLM, lease истёк, B перехватил: результат A отброшен, правки пишет B."""
    llm = SequencedLLM(calls=2)
    worker_env(llm)
    source_id = str(scenario.source_id)

    stale = asyncio.create_task(_run(scenario))
    await asyncio.wait_for(llm.started[0].wait(), WAIT_SECONDS)
    stale_token = (await _row(pg_sessionmaker, scenario)).lock_token
    await _expire_lease(pg_sessionmaker, scenario)

    owner = asyncio.create_task(_run(scenario))
    await asyncio.wait_for(llm.started[1].wait(), WAIT_SECONDS)
    owner_token = (await _row(pg_sessionmaker, scenario)).lock_token
    assert owner_token is not None
    assert owner_token != stale_token

    # A отвечает первым, но захват уже не его: ни правок, ни снятия чужого захвата.
    llm.gates[0].set()
    stale_result = await asyncio.wait_for(stale, WAIT_SECONDS)
    assert stale_result == {"source_id": source_id, "status": "busy"}
    assert await _suggestions_count(pg_sessionmaker, scenario) == 0
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.lock_token == owner_token

    llm.gates[1].set()
    owner_result = await asyncio.wait_for(owner, WAIT_SECONDS)
    assert owner_result == {"source_id": source_id, "status": "ok", "count": 2}

    # Повтор задачи A после busy видит completed и не зовёт LLM.
    retried = await _run(scenario)
    assert retried == {"source_id": source_id, "status": "ok", "count": 2}
    assert llm.calls == 2
    assert await _suggestions_count(pg_sessionmaker, scenario) == 2
    row = await _row(pg_sessionmaker, scenario)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.lock_token is None
