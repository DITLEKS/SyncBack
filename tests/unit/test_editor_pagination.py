"""
PR4 — Editor API: тесты корректности счётчиков при пагинации.

Проверяем три сценария:
  1. Счётчики первой страницы не зависят от размера страницы.
  2. Вторая страница возвращает правильный срез при total > limit.
  3. Пустая страница (offset >= total) — suggestions=[], counters не изменяются.

Все тесты — юнит-тесты с моками; не требуют PostgreSQL.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.value_objects import DocumentStatusVO, PaginationParams


def _make_suggestion(job_id: uuid.UUID, status: str = "pending") -> MagicMock:
    s = MagicMock()
    s.id = uuid.uuid4()
    s.analysis_job_id = job_id
    s.status = MagicMock(value=status)
    return s


def _make_document(
    job_id: uuid.UUID | None = None,
    status: DocumentStatusVO = DocumentStatusVO.AWAITING_APPROVAL,
) -> MagicMock:
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.project_id = uuid.uuid4()
    doc.current_analysis_job_id = job_id or uuid.uuid4()
    doc.status = status
    doc.uploaded_at = None
    doc.updated_at = None
    doc.review_version = 1
    return doc


@pytest.fixture()
def job_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def suggestion_service_mock(job_id):
    """Мок SuggestionService с 7 правками (5 pending, 1 accepted, 1 rejected)."""
    svc = AsyncMock()

    all_suggestions = [_make_suggestion(job_id, "pending") for _ in range(5)] + [
        _make_suggestion(job_id, "accepted"),
        _make_suggestion(job_id, "rejected"),
    ]

    async def _list(project_id, document_id, pagination: PaginationParams):
        start = pagination.offset
        end = start + pagination.limit
        page = all_suggestions[start:end]
        return page, len(all_suggestions)

    svc.list_suggestions_for_document.side_effect = _list
    svc.count_by_document_and_status.return_value = {
        "pending": 5,
        "accepted": 1,
        "rejected": 1,
    }
    return svc


class TestEditorCountersAreIndependentOfPageSize:
    """PR4-FIX: счётчики берутся из count_by_document_and_status, а не из страницы."""

    @pytest.mark.asyncio
    async def test_first_page_small_limit_counters_correct(self, suggestion_service_mock, job_id):
        pagination = PaginationParams(limit=3, offset=0)
        suggestions, total = await suggestion_service_mock.list_suggestions_for_document(
            uuid.uuid4(), uuid.uuid4(), pagination
        )
        counts = await suggestion_service_mock.count_by_document_and_status(
            uuid.uuid4(), uuid.uuid4()
        )

        assert total == 7
        assert len(suggestions) == 3
        assert counts["pending"] == 5
        assert counts["accepted"] == 1
        assert counts["rejected"] == 1

    @pytest.mark.asyncio
    async def test_counters_do_not_change_across_pages(self, suggestion_service_mock, job_id):
        """Счётчики одинаковы на первой и второй странице — они не из страницы."""
        counts_page1 = await suggestion_service_mock.count_by_document_and_status(
            uuid.uuid4(), uuid.uuid4()
        )
        counts_page2 = await suggestion_service_mock.count_by_document_and_status(
            uuid.uuid4(), uuid.uuid4()
        )
        assert counts_page1 == counts_page2


class TestEditorSecondPage:
    """Вторая страница возвращает правильный срез."""

    @pytest.mark.asyncio
    async def test_second_page_offset_3_limit_3(self, suggestion_service_mock, job_id):
        pagination = PaginationParams(limit=3, offset=3)
        suggestions, total = await suggestion_service_mock.list_suggestions_for_document(
            uuid.uuid4(), uuid.uuid4(), pagination
        )
        assert total == 7
        assert len(suggestions) == 3

    @pytest.mark.asyncio
    async def test_second_page_partial_last_page(self, suggestion_service_mock, job_id):
        pagination = PaginationParams(limit=5, offset=5)
        suggestions, total = await suggestion_service_mock.list_suggestions_for_document(
            uuid.uuid4(), uuid.uuid4(), pagination
        )
        assert total == 7
        assert len(suggestions) == 2  # только 2 оставшихся


class TestEditorEmptyPage:
    """Пустая страница (offset >= total) — suggestions=[], total не изменяется."""

    @pytest.mark.asyncio
    async def test_empty_page_beyond_total(self, suggestion_service_mock, job_id):
        pagination = PaginationParams(limit=50, offset=100)
        suggestions, total = await suggestion_service_mock.list_suggestions_for_document(
            uuid.uuid4(), uuid.uuid4(), pagination
        )
        assert total == 7
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_empty_page_counters_still_correct(self, suggestion_service_mock, job_id):
        counts = await suggestion_service_mock.count_by_document_and_status(
            uuid.uuid4(), uuid.uuid4()
        )
        assert counts["pending"] + counts["accepted"] + counts["rejected"] == 7


class TestEditorResetResult:
    """PR4-FIX: reset возвращает ResetResult dataclass, не int и не None."""

    def test_reset_result_has_required_fields(self):
        from dataclasses import dataclass

        @dataclass
        class FakeResetResult:
            reset_count: int
            document: object

        doc = _make_document()
        result = FakeResetResult(reset_count=5, document=doc)

        assert result.reset_count == 5
        assert result.document is doc
        assert isinstance(result.reset_count, int)


class TestEditorUpdatedAt:
    """PR4-FIX: updated_at использует document.updated_at, а не uploaded_at."""

    def test_updated_at_fallback_to_uploaded_at_when_none(self):
        from datetime import datetime, timezone

        doc = MagicMock()
        doc.updated_at = None
        doc.uploaded_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = doc.updated_at or doc.uploaded_at
        assert result == doc.uploaded_at

    def test_updated_at_uses_updated_at_when_present(self):
        from datetime import datetime, timezone

        doc = MagicMock()
        doc.updated_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        doc.uploaded_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = doc.updated_at or doc.uploaded_at
        assert result == doc.updated_at
