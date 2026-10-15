"""P0-2b: claim/release источника перед вызовом LLM (PostgreSQL)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.analysis_job_source import (
    SOURCE_RUN_COMPLETED,
    SOURCE_RUN_PENDING,
    SOURCE_RUN_PROCESSING,
    AnalysisJobSource,
)
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.analysis_job_source_repository import (
    AnalysisJobSourceRepository,
)

LEASE = 600


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
        project = Project(name="source-claim", owner_id=user.id)
        session.add(project)
        await session.flush()
        document = Document(
            project_id=project.id,
            name="claim.txt",
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
        state = JobState(job_id=job.id, source_id=uuid.uuid4())

    async with pg_sessionmaker() as session:
        await AnalysisJobSourceRepository(session).ensure(state.job_id, state.source_id)
        await session.commit()
    return state


async def _claim(sessionmaker: async_sessionmaker[AsyncSession], state: JobState) -> bool:
    async with sessionmaker() as session:
        won = await AnalysisJobSourceRepository(session).claim(
            state.job_id, state.source_id, LEASE
        )
        await session.commit()
        return won


async def _row(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState
) -> AnalysisJobSource:
    async with sessionmaker() as session:
        row = await AnalysisJobSourceRepository(session)._get(state.job_id, state.source_id)
        assert row is not None
        return row


async def _age_lock(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, seconds: int
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == state.job_id,
                AnalysisJobSource.source_id == state.source_id,
            )
            .values(locked_at=datetime.now(timezone.utc) - timedelta(seconds=seconds))
        )
        await session.commit()


async def test_concurrent_claim_has_one_winner(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    results = await asyncio.gather(
        _claim(pg_sessionmaker, job_state),
        _claim(pg_sessionmaker, job_state),
    )

    assert sorted(results) == [False, True]
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.locked_at is not None


async def test_fresh_lease_is_not_reclaimed(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _claim(pg_sessionmaker, job_state) is True
    assert await _claim(pg_sessionmaker, job_state) is False


async def test_expired_lease_is_taken_over(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _claim(pg_sessionmaker, job_state) is True
    await _age_lock(pg_sessionmaker, job_state, LEASE + 60)

    assert await _claim(pg_sessionmaker, job_state) is True


async def test_release_allows_immediate_reclaim(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _claim(pg_sessionmaker, job_state) is True
    async with pg_sessionmaker() as session:
        await AnalysisJobSourceRepository(session).release(job_state.job_id, job_state.source_id)
        await session.commit()

    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PENDING
    assert row.locked_at is None
    assert await _claim(pg_sessionmaker, job_state) is True


async def test_completed_source_cannot_be_claimed(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _claim(pg_sessionmaker, job_state) is True
    async with pg_sessionmaker() as session:
        repo = AnalysisJobSourceRepository(session)
        assert await repo.mark_completed(job_state.job_id, job_state.source_id, 2) is True
        await session.commit()

    # mark_completed сбрасывает lease: completed-строка не выглядит как «захваченная».
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.locked_at is None
    assert row.suggestions_count == 2

    assert await _claim(pg_sessionmaker, job_state) is False
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.locked_at is None

    # Даже «просроченный» locked_at на completed-строке не делает её доступной для захвата.
    await _age_lock(pg_sessionmaker, job_state, LEASE + 60)
    assert await _claim(pg_sessionmaker, job_state) is False
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.suggestions_count == 2
