"""
Unit-тесты PUT /editor/{document_id}/review  (atomic_review_save).

Используют FakeUnitOfWork — реальная БД не нужна.
Каждый тест проверяет одну ветку логики SuggestionService.atomic_review_save.
"""
from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any

import pytest

from app.domain.exceptions import (
    OptimisticLockError,
    ReviewNotCompleteError,
    SuggestionAlreadyDecidedError,
)
from app.domain.services.suggestion_service import SuggestionService
from app.domain.value_objects import (
    DocumentStatusVO,
    ReviewDecisions,
    SuggestionDecision,
    SuggestionStatusVO,
)

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

def _make_document(
    document_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    status: DocumentStatusVO = DocumentStatusVO.AWAITING_APPROVAL,
    review_version: int = 0,
):
    """Фабрика простого объекта-заглушки для Document."""
    class _Doc:
        pass
    doc = _Doc()
    doc.id = document_id
    doc.project_id = project_id
    doc.current_analysis_job_id = job_id
    doc.status = status
    doc.review_version = review_version
    return doc


def _make_suggestion(suggestion_id: uuid.UUID, job_id: uuid.UUID, status=SuggestionStatusVO.PENDING):
    class _Sug:
        pass
    s = _Sug()
    s.id = suggestion_id
    s.analysis_job_id = job_id
    s.status = status
    return s


class FakeDocumentRepo:
    def __init__(self, doc):
        self._doc = doc
        # None означает «CAS провалился»
        self._cas_result = doc

    def set_cas_fail(self):
        """Следующий вызов compare_and_increment_review_version вернёт None."""
        self._cas_result = None

    async def get_by_id(self, document_id):
        return self._doc if self._doc.id == document_id else None

    async def compare_and_increment_review_version(self, document_id, expected_version):
        if self._doc.review_version != expected_version:
            return None
        result = self._cas_result
        if result is not None:
            self._doc.review_version += 1
        return result

    async def update_status(self, document, status):
        document.status = status
        return document


class FakeSuggestionRepo:
    def __init__(self, suggestions: list):
        self._store: dict[uuid.UUID, Any] = {s.id: s for s in suggestions}
        self._pending_count = len(suggestions)

    def set_pending_count(self, n: int):
        self._pending_count = n

    async def bulk_update_status(self, decisions: ReviewDecisions):
        """Возвращает только те правки, что принадлежат текущему job и PENDING."""
        updated = []
        for sid in decisions.all_ids:
            s = self._store.get(sid)
            if s is not None and s.analysis_job_id == decisions.analysis_job_id:
                updated.append(s)
        return updated

    async def count_by_analysis_job_and_status(self, job_id, status):
        return self._pending_count


class FakeUnitOfWork:
    def __init__(self, doc, suggestions: list):
        self.documents = FakeDocumentRepo(doc)
        self.suggestions = FakeSuggestionRepo(suggestions)
        self._committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self):
        self._committed = True

    async def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ids():
    return {
        "project": uuid.uuid4(),
        "document": uuid.uuid4(),
        "job": uuid.uuid4(),
        "user": uuid.uuid4(),
        "s1": uuid.uuid4(),
        "s2": uuid.uuid4(),
        "s3": uuid.uuid4(),
    }


@pytest.fixture()
def document(ids):
    return _make_document(
        document_id=ids["document"],
        project_id=ids["project"],
        job_id=ids["job"],
        review_version=0,
    )


@pytest.fixture()
def suggestions(ids):
    return [
        _make_suggestion(ids["s1"], ids["job"]),
        _make_suggestion(ids["s2"], ids["job"]),
        _make_suggestion(ids["s3"], ids["job"]),
    ]


@pytest.fixture()
def uow(document, suggestions):
    return FakeUnitOfWork(document, suggestions)


@pytest.fixture()
def service(uow):
    return SuggestionService(uow)


# ---------------------------------------------------------------------------
# Tests: wrong review_version → OptimisticLockError
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_wrong_version_raises_optimistic_lock(service, ids):
    """review_version=99 не совпадает с document.review_version=0 → OptimisticLockError."""
    with pytest.raises(OptimisticLockError):
        await service.atomic_review_save(
            project_id=ids["project"],
            document_id=ids["document"],
            user_id=ids["user"],
            review_version=99,          # неверная версия
            accepted_ids=(ids["s1"],),
            rejected_ids=(),
            finalize=False,
        )


@pytest.mark.anyio
async def test_correct_version_commits_and_increments(service, uow, ids, document):
    """Правильная версия → commit вызван, review_version увеличилась."""
    result = await service.atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=0,
        accepted_ids=(ids["s1"],),
        rejected_ids=(ids["s2"],),
        finalize=False,
    )
    assert uow._committed is True
    assert document.review_version == 1
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    assert result.finalized is False


# ---------------------------------------------------------------------------
# Tests: already-decided suggestions
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_unknown_suggestion_raises_already_decided(service, ids):
    """UUID не из текущего job → bulk_update_status вернёт меньше записей → ошибка."""
    alien_id = uuid.uuid4()   # не принадлежит ни одному suggestion
    with pytest.raises(SuggestionAlreadyDecidedError):
        await service.atomic_review_save(
            project_id=ids["project"],
            document_id=ids["document"],
            user_id=ids["user"],
            review_version=0,
            accepted_ids=(alien_id,),
            rejected_ids=(),
            finalize=False,
        )


# ---------------------------------------------------------------------------
# Tests: finalize=True
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_finalize_with_pending_raises(service, uow, ids):
    """finalize=True при pending_count > 0 → ReviewNotCompleteError."""
    uow.suggestions.set_pending_count(2)   # остались необработанные
    with pytest.raises(ReviewNotCompleteError):
        await service.atomic_review_save(
            project_id=ids["project"],
            document_id=ids["document"],
            user_id=ids["user"],
            review_version=0,
            accepted_ids=(ids["s1"],),
            rejected_ids=(ids["s2"], ids["s3"]),
            finalize=True,
        )


@pytest.mark.anyio
async def test_finalize_no_pending_sets_ready(service, uow, ids, document):
    """finalize=True при pending_count=0 → статус документа READY, finalized=True."""
    uow.suggestions.set_pending_count(0)
    result = await service.atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=0,
        accepted_ids=(ids["s1"],),
        rejected_ids=(ids["s2"], ids["s3"]),
        finalize=True,
    )
    assert result.finalized is True
    assert document.status == DocumentStatusVO.READY
    assert uow._committed is True


# ---------------------------------------------------------------------------
# Tests: sequential saves increment review_version
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sequential_saves_increment_version(service, uow, ids, document):
    """Два последовательных вызова — review_version растёт с 0 до 2."""
    await service.atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=0,
        accepted_ids=(ids["s1"],),
        rejected_ids=(),
        finalize=False,
    )
    assert document.review_version == 1

    await service.atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=1,          # обновлённая версия
        accepted_ids=(ids["s2"],),
        rejected_ids=(),
        finalize=False,
    )
    assert document.review_version == 2
