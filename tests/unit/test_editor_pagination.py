"""PR4: тесты пагинации Editor API.

Покрывают три ключевых сценария:
  1. Первая страница — suggestions_total > len(suggestions) → counters
     берутся из агрегатного запроса, а не из длины страницы.
  2. Вторая страница (offset > 0) — suggestions не пустые, total совпадает
     с первой страницей, counters корректны.
  3. Пустая страница (offset >= total) — suggestions=[], total правильный,
     counters из агрегата (не 0 из-за пустой страницы).

Все тесты unit-level: сервисы заменены AsyncMock, HTTP-клиент не нужен.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
_PROJECT_ID = uuid.uuid4()
_DOC_ID = uuid.uuid4()
_JOB_ID = uuid.uuid4()


@dataclass
class _FakeDocument:
    id: uuid.UUID = _DOC_ID
    name: str = "test.docx"
    project_id: uuid.UUID = _PROJECT_ID
    status: Any = None  # set in fixture
    format: Any = None
    current_analysis_job_id: uuid.UUID = _JOB_ID
    uploaded_at: datetime = _NOW
    updated_at: datetime = _NOW
    review_version: int = 3


@dataclass
class _FakeSuggestion:
    id: uuid.UUID = None  # noqa: RUF009

    def __post_init__(self):
        if self.id is None:
            self.id = uuid.uuid4()


def _make_suggestion_service(
    *,
    page_items: list,
    total: int,
    pending: int,
    accepted: int,
    rejected: int,
) -> AsyncMock:
    """Создаёт мок SuggestionService с нужными возвращаемыми значениями."""
    svc = AsyncMock()
    svc.list_suggestions_for_document.return_value = (page_items, total)
    svc.count_by_document_and_status.return_value = {
        "pending": pending,
        "accepted": accepted,
        "rejected": rejected,
    }
    return svc


def _make_document_service(doc: _FakeDocument) -> AsyncMock:
    svc = AsyncMock()
    svc.get_document.return_value = doc
    # get_document_content / get_original_content вернут заглушку
    parsed = MagicMock()
    parsed.plain_text = "hello"
    parsed.sections = []
    svc.get_document_content.return_value = parsed
    svc.get_original_content.return_value = parsed
    return svc


# ---------------------------------------------------------------------------
# Import target (lazy, чтобы не тянуть весь FastAPI-стек)
# ---------------------------------------------------------------------------

def _import_handler():
    """Импортируем только функцию-обработчик, не приложение целиком."""
    from app.api.v1.routers.editor import get_editor_aggregate  # noqa: PLC0415
    return get_editor_aggregate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def doc():
    from app.domain.value_objects import DocumentStatusVO  # noqa: PLC0415

    class _FakeFormat:
        value = "docx"

    d = _FakeDocument()
    d.status = DocumentStatusVO.AWAITING_APPROVAL
    d.format = _FakeFormat()
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEditorPaginationCounters:
    """Счётчики должны приходить из агрегатного запроса, а не из страницы."""

    @pytest.mark.asyncio
    async def test_first_page_counters_from_aggregate(self, doc):
        """Первая страница из 2 правок; total=7; счётчики из агрегата."""
        items = [_FakeSuggestion() for _ in range(2)]
        suggestion_svc = _make_suggestion_service(
            page_items=items, total=7,
            pending=4, accepted=2, rejected=1,
        )
        document_svc = _make_document_service(doc)

        from app.domain.value_objects import PaginationParams  # noqa: PLC0415
        handler = _import_handler()

        result = await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=2,
            suggestions_offset=0,
        )

        assert result.suggestions_total == 7
        assert len(result.suggestions) == 2
        assert result.counters.pending == 4
        assert result.counters.accepted == 2
        assert result.counters.rejected == 1
        assert result.counters.total == 7  # total = suggestions_total, не len(page)

    @pytest.mark.asyncio
    async def test_second_page_counters_unchanged(self, doc):
        """Вторая страница (offset=2, limit=2); total и счётчики те же, что на стр. 1."""
        items = [_FakeSuggestion() for _ in range(2)]
        suggestion_svc = _make_suggestion_service(
            page_items=items, total=7,
            pending=4, accepted=2, rejected=1,
        )
        document_svc = _make_document_service(doc)

        handler = _import_handler()

        result = await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=2,
            suggestions_offset=2,
        )

        assert result.suggestions_total == 7
        assert len(result.suggestions) == 2
        # Счётчики НЕ должны равняться len(page)=2 ни для одного статуса
        assert result.counters.pending == 4
        assert result.counters.accepted == 2
        assert result.counters.rejected == 1
        assert result.counters.total == 7

        # Убеждаемся, что list_suggestions_for_document вызван с правильным offset
        call_args = suggestion_svc.list_suggestions_for_document.call_args
        pagination = call_args.args[2]  # PaginationParams
        assert pagination.offset == 2

    @pytest.mark.asyncio
    async def test_empty_page_counters_from_aggregate(self, doc):
        """Запрос за пределами total возвращает пустую страницу,
        но счётчики по-прежнему берутся из агрегатного запроса."""
        suggestion_svc = _make_suggestion_service(
            page_items=[], total=7,
            pending=4, accepted=2, rejected=1,
        )
        document_svc = _make_document_service(doc)

        handler = _import_handler()

        result = await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=2,
            suggestions_offset=100,  # далеко за границей
        )

        assert result.suggestions_total == 7
        assert result.suggestions == []
        # БЕЗ PR4-исправления здесь было бы pending=0, accepted=0, rejected=0
        assert result.counters.pending == 4
        assert result.counters.accepted == 2
        assert result.counters.rejected == 1
        assert result.counters.total == 7

    @pytest.mark.asyncio
    async def test_count_by_status_called_once(self, doc):
        """count_by_document_and_status должен вызываться ровно один раз за запрос."""
        suggestion_svc = _make_suggestion_service(
            page_items=[], total=0,
            pending=0, accepted=0, rejected=0,
        )
        document_svc = _make_document_service(doc)

        handler = _import_handler()

        await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=50,
            suggestions_offset=0,
        )

        suggestion_svc.count_by_document_and_status.assert_awaited_once_with(
            _PROJECT_ID, _DOC_ID
        )


class TestEditorUpdatedAt:
    """updated_at должен приходить из document.updated_at, не из uploaded_at."""

    @pytest.mark.asyncio
    async def test_updated_at_uses_document_field(self, doc):
        doc.updated_at = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
        doc.uploaded_at = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

        suggestion_svc = _make_suggestion_service(
            page_items=[], total=0, pending=0, accepted=0, rejected=0,
        )
        document_svc = _make_document_service(doc)

        handler = _import_handler()

        result = await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=50,
            suggestions_offset=0,
        )

        assert result.document.updated_at == doc.updated_at
        assert result.document.updated_at != doc.uploaded_at

    @pytest.mark.asyncio
    async def test_updated_at_fallback_to_uploaded_at(self, doc):
        """Если updated_at отсутствует — fallback на uploaded_at."""
        doc.updated_at = None  # нет поля
        doc.uploaded_at = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

        suggestion_svc = _make_suggestion_service(
            page_items=[], total=0, pending=0, accepted=0, rejected=0,
        )
        document_svc = _make_document_service(doc)

        handler = _import_handler()

        result = await handler(
            document_id=_DOC_ID,
            project=MagicMock(id=_PROJECT_ID),
            current_user=MagicMock(),
            document_service=document_svc,
            suggestion_service=suggestion_svc,
            suggestions_limit=50,
            suggestions_offset=0,
        )

        assert result.document.updated_at == doc.uploaded_at
