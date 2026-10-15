"""Гонки запуска анализа на реальном PostgreSQL (P0-1).

Каждый участник гонки работает в своей сессии, как два параллельных
POST /analysis-jobs. Итог один: либо одна задача и понятный отказ второму,
либо одна задача и те же id для обоих; необработанный IntegrityError (HTTP 500) недопустим.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.exceptions import AnalysisAlreadyRunningError, AnalysisJobAlreadyExistsError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass
class DraftState:
    project_id: uuid.UUID
    document_id: uuid.UUID


@pytest_asyncio.fixture
async def draft_state(pg_sessionmaker: async_sessionmaker[AsyncSession]) -> DraftState:
    """Черновик без задач анализа."""
    async with pg_sessionmaker() as session:
        user = User(email=f"it-{uuid.uuid4().hex}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(name="analysis-race", owner_id=user.id)
        session.add(project)
        await session.flush()
        document = Document(
            project_id=project.id,
            name="race.txt",
            format=DocumentFormat.TXT,
            storage_key=f"it/{uuid.uuid4()}.txt",
            size_bytes=1,
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(document)
        await session.commit()
        return DraftState(project_id=project.id, document_id=document.id)


async def _start(
    sessionmaker: async_sessionmaker[AsyncSession],
    state: DraftState,
    idempotency_key: str | None,
) -> uuid.UUID:
    """Запустить анализ и вернуть id задачи, как её увидит клиент (201 или 200)."""
    async with sessionmaker() as session:
        service = AnalysisJobService(SqlAlchemyUnitOfWork(session))
        try:
            job = await service.create_job(
                state.project_id, state.document_id, idempotency_key=idempotency_key
            )
        except AnalysisJobAlreadyExistsError as exc:
            return exc.job.id  # type: ignore[attr-defined]
        return job.id


async def _job_count(
    sessionmaker: async_sessionmaker[AsyncSession],
    state: DraftState,
    idempotency_key: str | None = None,
) -> int:
    async with sessionmaker() as session:
        stmt = (
            select(func.count())
            .select_from(AnalysisJob)
            .where(AnalysisJob.document_id == state.document_id)
        )
        if idempotency_key is not None:
            stmt = stmt.where(AnalysisJob.idempotency_key == idempotency_key)
        return (await session.execute(stmt)).scalar_one()


async def test_concurrent_start_without_key_has_one_winner(
    pg_sessionmaker: async_sessionmaker[AsyncSession], draft_state: DraftState
) -> None:
    results = await asyncio.gather(
        _start(pg_sessionmaker, draft_state, None),
        _start(pg_sessionmaker, draft_state, None),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, uuid.UUID)]
    losers = [r for r in results if isinstance(r, AnalysisAlreadyRunningError)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert await _job_count(pg_sessionmaker, draft_state) == 1

    async with pg_sessionmaker() as session:
        document = await session.get(Document, draft_state.document_id)
        assert document is not None
        assert document.current_analysis_job_id == winners[0]
        assert document.status.value == "draft"


async def test_concurrent_start_with_same_key_returns_same_job(
    pg_sessionmaker: async_sessionmaker[AsyncSession], draft_state: DraftState
) -> None:
    key = f"key-{uuid.uuid4().hex}"
    results = await asyncio.gather(
        _start(pg_sessionmaker, draft_state, key),
        _start(pg_sessionmaker, draft_state, key),
        return_exceptions=True,
    )

    assert all(isinstance(r, uuid.UUID) for r in results), results
    assert results[0] == results[1], results
    assert await _job_count(pg_sessionmaker, draft_state, key) == 1


async def test_sequential_start_with_same_key_is_idempotent(
    pg_sessionmaker: async_sessionmaker[AsyncSession], draft_state: DraftState
) -> None:
    key = f"key-{uuid.uuid4().hex}"
    first = await _start(pg_sessionmaker, draft_state, key)
    second = await _start(pg_sessionmaker, draft_state, key)

    assert first == second
    assert await _job_count(pg_sessionmaker, draft_state, key) == 1
