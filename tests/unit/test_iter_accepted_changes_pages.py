"""
Тесты для DocumentExportService._iter_accepted_changes_pages.

Проверяем:
  1. Нет job_id — метод возвращает [] без обращения к Репозиторию.
  2. Пустая Первая страница — останавливается сразу, возвращает [].
  3. Одна полная страница + пустая — два round-trip, собраны все изменения.
  4. Две полных страницы + частичная — три round-trip, стоп на неполной странице.
  5. Количество вызовов репозитория — верифицируем точное число round-trip.
  6. OFFSET растёт правильно: 0, PAGE_SIZE, 2*PAGE_SIZE.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.services.document_export_service import (
    DocumentExportService,
    _EXPORT_PAGE_SIZE,
)
from app.domain.value_objects import SuggestionStatusVO


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _make_suggestion(section_ref: str = "s1") -> MagicMock:
    """Минимальный стаб Suggestion-ORM-объекта."""
    s = MagicMock()
    s.section_ref = section_ref
    s.change_type = MagicMock()
    s.change_type.value = "update"
    s.old_text = "old"
    s.new_text = "new"
    return s


def _make_document(job_id: uuid.UUID | None = None) -> MagicMock:
    doc = MagicMock()
    doc.current_analysis_job_id = job_id or uuid.uuid4()
    doc.storage_key = "key"
    doc.title = "doc.docx"
    doc.format = MagicMock()
    return doc


def _make_service(pages: list[list]) -> tuple[DocumentExportService, AsyncMock]:
    """
    Создаёт DocumentExportService с моком uow.

    pages — список страниц, которые вернёт list_by_analysis_job_and_status_page
    при последовательных вызовах.
    """
    repo_mock = AsyncMock()
    repo_mock.list_by_analysis_job_and_status_page = AsyncMock(side_effect=pages)

    uow_mock = AsyncMock()
    uow_mock.suggestions = repo_mock
    # Поддержка async context manager (используется в async with self._uow)
    uow_mock.__aenter__ = AsyncMock(return_value=uow_mock)
    uow_mock.__aexit__ = AsyncMock(return_value=False)

    svc = DocumentExportService(
        uow=uow_mock,
        file_storage=AsyncMock(),
        exporter_registry=MagicMock(),
    )
    return svc, repo_mock


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_job_id_returns_empty() -> None:
    """Документ без current_analysis_job_id — возвращает [] без запросов."""
    svc, repo = _make_service(pages=[])
    doc = _make_document(job_id=None)
    doc.current_analysis_job_id = None

    result = await svc._iter_accepted_changes_pages(doc)

    assert result == []
    repo.list_by_analysis_job_and_status_page.assert_not_called()


@pytest.mark.asyncio
async def test_empty_first_page_returns_empty() -> None:
    """Первая страница пуста — возвращает [], один round-trip."""
    svc, repo = _make_service(pages=[[]])
    doc = _make_document()

    result = await svc._iter_accepted_changes_pages(doc)

    assert result == []
    assert repo.list_by_analysis_job_and_status_page.call_count == 1


@pytest.mark.asyncio
async def test_single_partial_page_returns_all_items() -> None:
    """Одна неполная страница — два round-trip (парциальная + пустая не нужна,
    т.к. проверка len(page) < PAGE_SIZE останавливает цикл)."""
    items = [_make_suggestion(f"s{i}") for i in range(3)]
    svc, repo = _make_service(pages=[items])
    doc = _make_document()

    result = await svc._iter_accepted_changes_pages(doc)

    assert len(result) == 3
    assert all(isinstance(c, AppliedChange) for c in result)
    assert repo.list_by_analysis_job_and_status_page.call_count == 1


@pytest.mark.asyncio
async def test_full_page_then_empty_makes_two_roundtrips() -> None:
    """Полная страница + пустая — два round-trip, все записи собраны."""
    full_page = [_make_suggestion(f"s{i}") for i in range(_EXPORT_PAGE_SIZE)]
    svc, repo = _make_service(pages=[full_page, []])
    doc = _make_document()

    result = await svc._iter_accepted_changes_pages(doc)

    assert len(result) == _EXPORT_PAGE_SIZE
    assert repo.list_by_analysis_job_and_status_page.call_count == 2


@pytest.mark.asyncio
async def test_two_full_pages_then_partial_makes_three_roundtrips() -> None:
    """Две полные + частичная — три round-trip."""
    full_page = [_make_suggestion(f"s{i}") for i in range(_EXPORT_PAGE_SIZE)]
    partial_page = [_make_suggestion(f"p{i}") for i in range(7)]
    svc, repo = _make_service(pages=[full_page, full_page, partial_page])
    doc = _make_document()

    result = await svc._iter_accepted_changes_pages(doc)

    assert len(result) == 2 * _EXPORT_PAGE_SIZE + 7
    assert repo.list_by_analysis_job_and_status_page.call_count == 3


@pytest.mark.asyncio
async def test_offset_increments_correctly() -> None:
    """Проверяем, что offset растёт: 0, PAGE_SIZE, 2*PAGE_SIZE."""
    full_page = [_make_suggestion() for _ in range(_EXPORT_PAGE_SIZE)]
    partial_page = [_make_suggestion()]
    svc, repo = _make_service(pages=[full_page, full_page, partial_page])
    doc = _make_document()
    job_id = doc.current_analysis_job_id

    await svc._iter_accepted_changes_pages(doc)

    calls = repo.list_by_analysis_job_and_status_page.call_args_list
    assert calls[0].kwargs["offset"] == 0 or calls[0].args[2] == 0
    # Проверяем через keyword или positional
    def _get_offset(call, pos=3):
        if "offset" in call.kwargs:
            return call.kwargs["offset"]
        return call.args[pos]

    assert _get_offset(calls[0]) == 0
    assert _get_offset(calls[1]) == _EXPORT_PAGE_SIZE
    assert _get_offset(calls[2]) == 2 * _EXPORT_PAGE_SIZE


@pytest.mark.asyncio
async def test_correct_status_passed_to_repo() -> None:
    """Репозиторий всегда получает SuggestionStatusVO.ACCEPTED."""
    svc, repo = _make_service(pages=[[]])
    doc = _make_document()

    await svc._iter_accepted_changes_pages(doc)

    call = repo.list_by_analysis_job_and_status_page.call_args
    # status — второй positional или keyword аргумент
    status = call.kwargs.get("status") or call.args[1]
    assert status == SuggestionStatusVO.ACCEPTED
