"""P0-2: повторная доставка задачи источника не дублирует правки (PostgreSQL)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.analysis_job_source import AnalysisJobSource
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.analysis_job_source_repository import (
    AnalysisJobSourceRepository,
)


@dataclass
class JobState:
    job_id: uuid.UUID
    source_id: uuid.UUID


@pytest_asyncio.fixture
async def job_state(pg_sessionmaker: async_sessionmaker[AsyncSession]) -> JobState:
    async with pg_sessionmaker() as session:
        user = User(email=f"it-{uuid.uuid4().hex}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(name="source-idem", owner_id=user.id)
        session.add(project)
        await session.flush()
        document = Document(
            project_id=project.id,
            name="idem.txt",
            format=DocumentFormat.TXT,
            storage_key=f"it/{uuid.uuid4()}.txt",
            size_bytes=1,
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(document)
        await session.flush()
        job = AnalysisJob(document_id=document.id)
        session.add(job)
        await session.commit()
        return JobState(job_id=job.id, source_id=uuid.uuid4())


async def _complete(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, count: int
) -> bool:
    async with sessionmaker() as session:
        repo = AnalysisJobSourceRepository(session)
        await repo.ensure(state.job_id, state.source_id)
        first = await repo.mark_completed(state.job_id, state.source_id, count)
        await session.commit()
        return first


async def test_ensure_is_idempotent(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    async with pg_sessionmaker() as session:
        repo = AnalysisJobSourceRepository(session)
        first = await repo.ensure(job_state.job_id, job_state.source_id)
        second = await repo.ensure(job_state.job_id, job_state.source_id)
        await session.commit()
        assert first.id == second.id

    async with pg_sessionmaker() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(AnalysisJobSource)
                .where(AnalysisJobSource.job_id == job_state.job_id)
            )
        ).scalar_one()
        assert total == 1


async def test_redelivery_after_completion_is_skipped(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _complete(pg_sessionmaker, job_state, 3) is True
    assert await _complete(pg_sessionmaker, job_state, 5) is False

    async with pg_sessionmaker() as session:
        repo = AnalysisJobSourceRepository(session)
        assert await repo.get_completed_count(job_state.job_id, job_state.source_id) == 3


async def test_concurrent_completion_has_one_winner(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    async with pg_sessionmaker() as session:
        await AnalysisJobSourceRepository(session).ensure(job_state.job_id, job_state.source_id)
        await session.commit()

    results = await asyncio.gather(
        _complete(pg_sessionmaker, job_state, 2),
        _complete(pg_sessionmaker, job_state, 2),
    )

    assert sorted(results) == [False, True]
