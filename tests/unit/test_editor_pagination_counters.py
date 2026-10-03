"""
PR4 — тест пагинации счётчиков Editor API.

Покрывает ошибку: _safe_count_by_status ранее итерировал страницу вместо
вызова count_by_document_and_status(), давая неверные значения при offset > 0.

Два сценария:
  1. Вторая страница: счётчики должны отражать ВСЕ правки документа, не только
     те, что вошли в текущую страницу.
  2. Пустая вторая страница: счётчики корректно возвращают числа по всему job,
     даже если suggestions на этой странице = [].
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_suggestion(status: str = "pending") -> MagicMock:
    s = MagicMock()
    s.id = uuid.uuid4()
    s.status = MagicMock(value=status)
    s.analysis_job_id = uuid.uuid4()
    return s


@pytest.mark.asyncio
async def test_counters_on_second_page_are_full_document_counts():
    """
    Запрашиваем вторую страницу (offset=50, limit=50).
    На странице всего 2 правки (pending), но в документе их 120:
    pending=80, accepted=30, rejected=10.
    Счётчики должны вернуть {pending:80, accepted:30, rejected:10}.
    """
    from app.domain.services.suggestion_service import SuggestionService
    from app.domain.interfaces.unit_of_work import IUnitOfWork

    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Мок документа с current_analysis_job_id
    mock_doc = MagicMock()
    mock_doc.project_id = project_id
    mock_doc.current_analysis_job_id = job_id

    # Вторая страница: только 2 pending-правки
    page2_suggestions = [_make_suggestion("pending"), _make_suggestion("pending")]

    mock_suggestions = MagicMock()
    mock_suggestions.get_by_id = AsyncMock(return_value=mock_doc)
    mock_suggestions.list_with_total = AsyncMock(return_value=(page2_suggestions, 82))
    mock_suggestions.count_by_analysis_job_and_status = AsyncMock(
        side_effect=[
            80,  # PENDING
            30,  # ACCEPTED
            10,  # REJECTED
        ]
    )

    mock_documents = MagicMock()
    mock_documents.get_by_id = AsyncMock(return_value=mock_doc)

    mock_uow = AsyncMock(spec=IUnitOfWork)
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.documents = mock_documents
    mock_uow.suggestions = mock_suggestions

    service = SuggestionService(mock_uow)
    counts = await service.count_by_document_and_status(project_id, document_id)

    assert counts["pending"] == 80, "pending должен отражать все правки, не только страницу"
    assert counts["accepted"] == 30
    assert counts["rejected"] == 10
    # count_by_analysis_job_and_status вызван три раза (по одному на статус)
    assert mock_suggestions.count_by_analysis_job_and_status.call_count == 3


@pytest.mark.asyncio
async def test_counters_on_empty_second_page_return_full_counts():
    """
    Пустая вторая страница (offset=100, suggestions=[]).
    Счётчики всё равно должны вернуть реальные числа по job, не нули.
    """
    from app.domain.services.suggestion_service import SuggestionService
    from app.domain.interfaces.unit_of_work import IUnitOfWork

    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_doc = MagicMock()
    mock_doc.project_id = project_id
    mock_doc.current_analysis_job_id = job_id

    mock_suggestions = MagicMock()
    mock_suggestions.get_by_id = AsyncMock(return_value=mock_doc)
    mock_suggestions.list_with_total = AsyncMock(return_value=([], 0))
    mock_suggestions.count_by_analysis_job_and_status = AsyncMock(
        side_effect=[
            5,  # PENDING
            45,  # ACCEPTED
            0,  # REJECTED
        ]
    )

    mock_documents = MagicMock()
    mock_documents.get_by_id = AsyncMock(return_value=mock_doc)

    mock_uow = AsyncMock(spec=IUnitOfWork)
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.documents = mock_documents
    mock_uow.suggestions = mock_suggestions

    service = SuggestionService(mock_uow)
    counts = await service.count_by_document_and_status(project_id, document_id)

    assert counts["pending"] == 5
    assert counts["accepted"] == 45
    assert counts["rejected"] == 0


@pytest.mark.asyncio
async def test_counters_return_zeros_when_no_active_job():
    """
    Документ без current_analysis_job_id → все счётчики 0,
    count_by_analysis_job_and_status не вызывается вовсе.
    """
    from app.domain.services.suggestion_service import SuggestionService
    from app.domain.interfaces.unit_of_work import IUnitOfWork

    project_id = uuid.uuid4()
    document_id = uuid.uuid4()

    mock_doc = MagicMock()
    mock_doc.project_id = project_id
    mock_doc.current_analysis_job_id = None  # нет активного job

    mock_suggestions = MagicMock()
    mock_suggestions.count_by_analysis_job_and_status = AsyncMock()

    mock_documents = MagicMock()
    mock_documents.get_by_id = AsyncMock(return_value=mock_doc)

    mock_uow = AsyncMock(spec=IUnitOfWork)
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.documents = mock_documents
    mock_uow.suggestions = mock_suggestions

    service = SuggestionService(mock_uow)
    counts = await service.count_by_document_and_status(project_id, document_id)

    assert counts == {"pending": 0, "accepted": 0, "rejected": 0}
    mock_suggestions.count_by_analysis_job_and_status.assert_not_called()
