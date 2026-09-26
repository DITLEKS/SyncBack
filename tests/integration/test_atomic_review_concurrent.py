"""
Интеграционные тесты: конкурентные запросы к atomic_review_save.

Проверяют поведение оптимистичной блокировки при параллельном запуске двух
coroutine в одном event loop через asyncio.gather. Используют FakeUnitOfWork
с thread-unsafe state — этого достаточно для проверки логики CAS без реальной БД.

Для тестов с реальной PostgreSQL (row-level locking через SELECT ... FOR UPDATE)
см. TODO в конце файла.
"""
from __future__ import annotations

import asyncio
import uuid
from types import TracebackType
from typing import Any

import pytest

from app.domain.exceptions import OptimisticLockError, SuggestionAlreadyDecidedError
from app.domain.services.suggestion_service import SuggestionService
from app.domain.value_objects import (
    DocumentStatusVO,
    ReviewDecisions,
    SuggestionStatusVO,
)

# ---------------------------------------------------------------------------
# Stubs (те же что в test_atomic_review.py, но с атомарным CAS-счётчиком)
# ---------------------------------------------------------------------------

def _doc(document_id, project_id, job_id, review_version=0):
    class _D:
        pass
    d = _D()
    d.id = document_id
    d.project_id = project_id
    d.current_analysis_job_id = job_id
    d.status = DocumentStatusVO.AWAITING_APPROVAL
    d.review_version = review_version
    return d


def _sug(suggestion_id, job_id):
    class _S:
        pass
    s = _S()
    s.id = suggestion_id
    s.analysis_job_id = job_id
    s.status = SuggestionStatusVO.PENDING
    return s


class _ConcurrentDocRepo:
    """CAS с честной проверкой версии — эмулирует UPDATE ... WHERE review_version=N."""

    def __init__(self, doc):
        self._doc = doc
        self._lock = asyncio.Lock()

    async def get_by_id(self, document_id):
        return self._doc if self._doc.id == document_id else None

    async def compare_and_increment_review_version(self, document_id, expected_version):
        async with self._lock:
            if self._doc.review_version != expected_version:
                return None          # CAS failed
            self._doc.review_version += 1
            return self._doc

    async def update_status(self, document, status):
        document.status = status
        return document


class _SuggestionRepo:
    def __init__(self, suggestions):
        self._store: dict[uuid.UUID, Any] = {s.id: s for s in suggestions}
        self._pending_count = 0

    async def bulk_update_status(self, decisions: ReviewDecisions):
        updated = []
        for sid in decisions.all_ids:
            s = self._store.get(sid)
            if s is not None and s.analysis_job_id == decisions.analysis_job_id:
                updated.append(s)
        return updated

    async def count_by_analysis_job_and_status(self, job_id, status):
        return self._pending_count


class _ConcurrentUoW:
    """UoW, который один на оба параллельных вызова — общий doc-репозиторий."""

    def __init__(self, doc, suggestions):
        self.documents = _ConcurrentDocRepo(doc)
        self.suggestions = _SuggestionRepo(suggestions)
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cids():
    return {
        "project":  uuid.uuid4(),
        "document": uuid.uuid4(),
        "job":      uuid.uuid4(),
        "user":     uuid.uuid4(),
        "s1":       uuid.uuid4(),
        "s2":       uuid.uuid4(),
    }


@pytest.fixture()
def shared_uow(cids):
    doc = _doc(cids["document"], cids["project"], cids["job"], review_version=0)
    sugs = [_sug(cids["s1"], cids["job"]), _sug(cids["s2"], cids["job"])]
    return _ConcurrentUoW(doc, sugs)


# ---------------------------------------------------------------------------
# TEST 1: Оба запроса с version=0 — ровно один побеждает, второй — OptimisticLockError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_concurrent_same_version_one_wins_one_fails(shared_uow, cids):
    """
    Два asyncio-таска стартуют с одинаковым review_version=0.
    CAS гарантирует: только первый, захвативший asyncio.Lock, получит
    инкрементированную версию; второй получит OptimisticLockError.
    """
    service = SuggestionService(shared_uow)

    async def _call(accepted_id: uuid.UUID):
        return await service.atomic_review_save(
            project_id=cids["project"],
            document_id=cids["document"],
            user_id=cids["user"],
            review_version=0,
            accepted_ids=(accepted_id,),
            rejected_ids=(),
            finalize=False,
        )

    results = await asyncio.gather(
        _call(cids["s1"]),
        _call(cids["s2"]),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures  = [r for r in results if isinstance(r, OptimisticLockError)]

    assert len(successes) == 1, "Ровно один запрос должен пройти"
    assert len(failures)  == 1, "Ровно один запрос должен получить OptimisticLockError"
    # После победившего запроса версия должна стать 1
    assert shared_uow.documents._doc.review_version == 1


# ---------------------------------------------------------------------------
# TEST 2: Второй запрос с актуальной версией после первого — оба проходят
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sequential_concurrent_both_succeed(shared_uow, cids):
    """
    Первый запрос: version=0 → OK, версия становится 1.
    Второй запрос: version=1 → OK, версия становится 2.
    Последовательное выполнение — оба коммита должны пройти.
    """
    service = SuggestionService(shared_uow)

    await service.atomic_review_save(
        project_id=cids["project"],
        document_id=cids["document"],
        user_id=cids["user"],
        review_version=0,
        accepted_ids=(cids["s1"],),
        rejected_ids=(),
        finalize=False,
    )
    assert shared_uow.documents._doc.review_version == 1

    await service.atomic_review_save(
        project_id=cids["project"],
        document_id=cids["document"],
        user_id=cids["user"],
        review_version=1,
        accepted_ids=(cids["s2"],),
        rejected_ids=(),
        finalize=False,
    )
    assert shared_uow.documents._doc.review_version == 2
    assert shared_uow.commit_count == 2


# ---------------------------------------------------------------------------
# TEST 3: Три параллельных запроса с одной версией — ровно один победитель
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_three_concurrent_one_wins(cids):
    """
    Три таска одновременно с version=0.
    Только один проходит, два получают OptimisticLockError.
    """
    doc = _doc(cids["document"], cids["project"], cids["job"], review_version=0)
    sugs = [_sug(cids["s1"], cids["job"]), _sug(cids["s2"], cids["job"])]
    uow = _ConcurrentUoW(doc, sugs)
    service = SuggestionService(uow)

    alien1, alien2 = uuid.uuid4(), uuid.uuid4()  # не в store → вызовет ошибку если дойдёт

    async def _call():
        return await service.atomic_review_save(
            project_id=cids["project"],
            document_id=cids["document"],
            user_id=cids["user"],
            review_version=0,
            accepted_ids=(cids["s1"],),
            rejected_ids=(),
            finalize=False,
        )

    results = await asyncio.gather(_call(), _call(), _call(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    lock_errors = [r for r in results if isinstance(r, OptimisticLockError)]

    assert len(successes)   == 1
    assert len(lock_errors) == 2
    assert uow.documents._doc.review_version == 1


# ---------------------------------------------------------------------------
# TEST 4: Стаггеред race — первый получил lock, второй стартует сразу после
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_staggered_race_second_sees_updated_version(cids):
    """
    Первый запрос успевает до второго (await asyncio.sleep(0)).
    Второй стартует с устаревшей version=0 — получает OptimisticLockError.
    """
    doc = _doc(cids["document"], cids["project"], cids["job"], review_version=0)
    sugs = [_sug(cids["s1"], cids["job"])]
    uow = _ConcurrentUoW(doc, sugs)
    service = SuggestionService(uow)

    async def _first():
        return await service.atomic_review_save(
            project_id=cids["project"],
            document_id=cids["document"],
            user_id=cids["user"],
            review_version=0,
            accepted_ids=(cids["s1"],),
            rejected_ids=(),
            finalize=False,
        )

    async def _second():
        await asyncio.sleep(0)   # yield — даём первому захватить lock
        return await service.atomic_review_save(
            project_id=cids["project"],
            document_id=cids["document"],
            user_id=cids["user"],
            review_version=0,    # намеренно устаревшая
            accepted_ids=(cids["s1"],),
            rejected_ids=(),
            finalize=False,
        )

    results = await asyncio.gather(_first(), _second(), return_exceptions=True)
    successes  = [r for r in results if not isinstance(r, Exception)]
    lock_errs  = [r for r in results if isinstance(r, OptimisticLockError)]

    assert len(successes) == 1
    assert len(lock_errs) == 1


# ---------------------------------------------------------------------------
# TODO: PostgreSQL-уровень (для CI с реальной БД)
# ---------------------------------------------------------------------------
# Следующий шаг — тесты с реальным SqlAlchemyUnitOfWork + PostgreSQL через
# pytest-asyncio + testcontainers (или DATABASE_URL из .env.test):
#
# @pytest.mark.integration   # маркер для slow-tests
# async def test_pg_concurrent_review_version(pg_uow_factory, ...):
#     uow1 = pg_uow_factory()
#     uow2 = pg_uow_factory()   # независимые сессии
#     ...
#     # Реальный SELECT ... FOR UPDATE SKIP LOCKED / NOWAIT
