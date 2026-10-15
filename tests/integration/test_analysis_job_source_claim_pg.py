"""claim/release/mark_completed источника перед вызовом LLM (PostgreSQL)."""

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


async def _claim(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState
) -> uuid.UUID | None:
    async with sessionmaker() as session:
        token = await AnalysisJobSourceRepository(session).claim(
            state.job_id, state.source_id, LEASE
        )
        await session.commit()
        return token


async def _release(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, token: uuid.UUID
) -> None:
    async with sessionmaker() as session:
        await AnalysisJobSourceRepository(session).release(state.job_id, state.source_id, token)
        await session.commit()


async def _complete(
    sessionmaker: async_sessionmaker[AsyncSession],
    state: JobState,
    token: uuid.UUID,
    count: int,
) -> bool:
    async with sessionmaker() as session:
        won = await AnalysisJobSourceRepository(session).mark_completed(
            state.job_id, state.source_id, token, count
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

    winners = [token for token in results if token is not None]
    assert len(winners) == 1
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.locked_at is not None
    assert row.lock_token == winners[0]


async def test_fresh_lease_is_not_reclaimed(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _claim(pg_sessionmaker, job_state) is not None
    assert await _claim(pg_sessionmaker, job_state) is None


async def test_expired_lease_is_taken_over(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    first = await _claim(pg_sessionmaker, job_state)
    assert first is not None
    await _age_lock(pg_sessionmaker, job_state, LEASE + 60)

    second = await _claim(pg_sessionmaker, job_state)
    assert second is not None
    assert second != first


async def test_release_allows_immediate_reclaim(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    token = await _claim(pg_sessionmaker, job_state)
    assert token is not None
    await _release(pg_sessionmaker, job_state, token)

    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PENDING
    assert row.locked_at is None
    assert row.lock_token is None
    assert await _claim(pg_sessionmaker, job_state) is not None


async def test_completed_source_cannot_be_claimed(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    token = await _claim(pg_sessionmaker, job_state)
    assert token is not None
    assert await _complete(pg_sessionmaker, job_state, token, 2) is True

    # mark_completed сбрасывает lease: completed-строка не выглядит как «захваченная».
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.locked_at is None
    assert row.lock_token is None
    assert row.suggestions_count == 2

    assert await _claim(pg_sessionmaker, job_state) is None
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.locked_at is None

    # Даже «просроченный» locked_at на completed-строке не делает её доступной для захвата.
    await _age_lock(pg_sessionmaker, job_state, LEASE + 60)
    assert await _claim(pg_sessionmaker, job_state) is None
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.suggestions_count == 2


async def test_taken_over_worker_cannot_complete_or_release(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    """A захватил, lease истёк, B перехватил: A больше ничего не меняет, B завершает."""
    stale = await _claim(pg_sessionmaker, job_state)
    assert stale is not None
    await _age_lock(pg_sessionmaker, job_state, LEASE + 60)
    owner = await _claim(pg_sessionmaker, job_state)
    assert owner is not None

    await _release(pg_sessionmaker, job_state, stale)
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.lock_token == owner

    assert await _complete(pg_sessionmaker, job_state, stale, 5) is False
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.lock_token == owner

    assert await _complete(pg_sessionmaker, job_state, owner, 2) is True
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.suggestions_count == 2


async def test_pending_source_cannot_be_completed_without_claim(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    assert await _complete(pg_sessionmaker, job_state, uuid.uuid4(), 1) is False
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PENDING
