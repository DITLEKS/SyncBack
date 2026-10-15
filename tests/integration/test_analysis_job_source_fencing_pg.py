"""Захват источника привязан к claim_token: поздний владелец не трогает чужой захват (PostgreSQL)."""

from __future__ import annotations

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
        project = Project(name="source-fencing", owner_id=user.id)
        session.add(project)
        await session.flush()
        document = Document(
            project_id=project.id,
            name="fencing.txt",
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
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, token: uuid.UUID
) -> bool:
    async with sessionmaker() as session:
        won = await AnalysisJobSourceRepository(session).claim(
            state.job_id, state.source_id, LEASE, claim_token=token
        )
        await session.commit()
        return won


async def _release(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, token: uuid.UUID
) -> None:
    async with sessionmaker() as session:
        await AnalysisJobSourceRepository(session).release(
            state.job_id, state.source_id, claim_token=token
        )
        await session.commit()


async def _complete(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState, token: uuid.UUID
) -> bool:
    async with sessionmaker() as session:
        won = await AnalysisJobSourceRepository(session).mark_completed(
            state.job_id, state.source_id, 1, claim_token=token
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


async def _expire_lease(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == state.job_id,
                AnalysisJobSource.source_id == state.source_id,
            )
            .values(locked_at=datetime.now(timezone.utc) - timedelta(seconds=LEASE + 60))
        )
        await session.commit()


async def _takeover(
    sessionmaker: async_sessionmaker[AsyncSession], state: JobState
) -> tuple[uuid.UUID, uuid.UUID]:
    stale_token, new_token = uuid.uuid4(), uuid.uuid4()
    assert await _claim(sessionmaker, state, stale_token) is True
    await _expire_lease(sessionmaker, state)
    assert await _claim(sessionmaker, state, new_token) is True
    return stale_token, new_token


async def test_stale_owner_cannot_release_new_claim(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    stale_token, new_token = await _takeover(pg_sessionmaker, job_state)

    await _release(pg_sessionmaker, job_state, stale_token)

    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING
    assert row.claim_token == new_token
    assert await _claim(pg_sessionmaker, job_state, uuid.uuid4()) is False


async def test_stale_owner_cannot_complete_new_claim(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    stale_token, new_token = await _takeover(pg_sessionmaker, job_state)

    assert await _complete(pg_sessionmaker, job_state, stale_token) is False
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PROCESSING

    assert await _complete(pg_sessionmaker, job_state, new_token) is True
    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_COMPLETED
    assert row.claim_token is None


async def test_owner_release_frees_source(
    pg_sessionmaker: async_sessionmaker[AsyncSession], job_state: JobState
) -> None:
    token = uuid.uuid4()
    assert await _claim(pg_sessionmaker, job_state, token) is True

    await _release(pg_sessionmaker, job_state, token)

    row = await _row(pg_sessionmaker, job_state)
    assert row.status == SOURCE_RUN_PENDING
    assert row.claim_token is None
    assert await _claim(pg_sessionmaker, job_state, uuid.uuid4()) is True
