"""Гонки при ревью на реальном PostgreSQL.

Каждый участник гонки работает в своей сессии и своём соединении, как два
параллельных HTTP-запроса. На SQLite такие тесты не воспроизводят блокировки строк.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.exceptions import OptimisticLockError, SuggestionAlreadyDecidedError
from app.domain.services.suggestion_service import SuggestionService
from app.domain.value_objects import SuggestionStatusVO
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.models.user import User
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.conftest import seed_review


@dataclass
class ReviewState:
    project_id: uuid.UUID
    document_id: uuid.UUID
    user_id: uuid.UUID
    suggestion_ids: list[uuid.UUID]


@pytest_asyncio.fixture
async def review_state(pg_sessionmaker: async_sessionmaker[AsyncSession]) -> ReviewState:
    """Документ в awaiting_approval с завершённым анализом и тремя pending-правками."""
    async with pg_sessionmaker() as session:
        user = User(email=f"it-{uuid.uuid4().hex}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(name="review-race", owner_id=user.id)
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
        ids = (project.id, document.id, user.id)
    suggestion_ids = await seed_review(pg_sessionmaker, ids[1], 3)
    return ReviewState(
        project_id=ids[0], document_id=ids[1], user_id=ids[2], suggestion_ids=suggestion_ids
    )


async def _review_save(
    sessionmaker: async_sessionmaker[AsyncSession],
    state: ReviewState,
    *,
    review_version: int,
    accepted: tuple[uuid.UUID, ...],
) -> int:
    async with sessionmaker() as session:
        result = await SuggestionService(SqlAlchemyUnitOfWork(session)).atomic_review_save(
            project_id=state.project_id,
            document_id=state.document_id,
            user_id=state.user_id,
            review_version=review_version,
            accepted_ids=accepted,
            finalize=False,
        )
        return result.document.review_version


async def _statuses(
    sessionmaker: async_sessionmaker[AsyncSession], ids: list[uuid.UUID]
) -> dict[uuid.UUID, SuggestionStatus]:
    async with sessionmaker() as session:
        rows = await session.execute(
            select(Suggestion.id, Suggestion.status).where(Suggestion.id.in_(ids))
        )
        return {row.id: row.status for row in rows}


async def test_concurrent_review_save_with_same_version_has_one_winner(
    pg_sessionmaker: async_sessionmaker[AsyncSession], review_state: ReviewState
) -> None:
    first, second, _ = review_state.suggestion_ids
    results = await asyncio.gather(
        _review_save(pg_sessionmaker, review_state, review_version=0, accepted=(first,)),
        _review_save(pg_sessionmaker, review_state, review_version=0, accepted=(second,)),
        return_exceptions=True,
    )

    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, OptimisticLockError)]
    assert winners == [1], results
    assert len(losers) == 1, results

    async with pg_sessionmaker() as session:
        document = await session.get(Document, review_state.document_id)
        assert document is not None
        assert document.review_version == 1

    # Решение проигравшего откатилось вместе с его транзакцией.
    statuses = await _statuses(pg_sessionmaker, [first, second])
    assert sorted(s.value for s in statuses.values()) == ["accepted", "pending"]


async def test_stale_review_version_is_rejected_after_commit(
    pg_sessionmaker: async_sessionmaker[AsyncSession], review_state: ReviewState
) -> None:
    first, second, third = review_state.suggestion_ids
    assert (
        await _review_save(pg_sessionmaker, review_state, review_version=0, accepted=(first,)) == 1
    )

    with pytest.raises(OptimisticLockError):
        await _review_save(pg_sessionmaker, review_state, review_version=0, accepted=(second,))

    assert (
        await _review_save(pg_sessionmaker, review_state, review_version=1, accepted=(third,)) == 2
    )
    statuses = await _statuses(pg_sessionmaker, [first, second, third])
    assert statuses == {
        first: SuggestionStatus.ACCEPTED,
        second: SuggestionStatus.PENDING,
        third: SuggestionStatus.ACCEPTED,
    }


async def test_concurrent_accept_and_reject_of_same_suggestion(
    pg_sessionmaker: async_sessionmaker[AsyncSession], review_state: ReviewState
) -> None:
    target = review_state.suggestion_ids[0]

    async def decide(status: SuggestionStatusVO) -> SuggestionStatusVO:
        async with pg_sessionmaker() as session:
            await SuggestionService(SqlAlchemyUnitOfWork(session)).patch_suggestions(
                project_id=review_state.project_id,
                document_id=review_state.document_id,
                user_id=review_state.user_id,
                target_status=status,
                ids=[target],
            )
        return status

    results = await asyncio.gather(
        decide(SuggestionStatusVO.ACCEPTED),
        decide(SuggestionStatusVO.REJECTED),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, SuggestionStatusVO)]
    losers = [r for r in results if isinstance(r, SuggestionAlreadyDecidedError)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results

    statuses = await _statuses(pg_sessionmaker, [target])
    assert statuses[target].value == winners[0].value
