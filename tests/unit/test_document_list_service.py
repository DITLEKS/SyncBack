"""
Unit-тесты для DocumentService.list_all_for_user.

Покрываемые инварианты:
1. Метод делегирует вызов репозиторию и возвращает его результат без изменений.
2. Значение limit ограничивается 200 вне зависимости от переданного значения.
3. P1: row-структура содержит все поля, необходимые роутеру my_documents
   (document, project_name, suggestions_*).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.document_service import DocumentService


def _make_service() -> tuple[DocumentService, AsyncMock]:
    """Создаёт DocumentService с замоканным репозиторием."""
    repo = AsyncMock()
    repo.list_all_for_user.return_value = ([], 0)
    storage = AsyncMock()
    svc = DocumentService(
        document_repository=repo,
        file_storage=storage,
    )
    return svc, repo


def _make_doc_row(
    doc_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    """Возвращает row-словарь в формате, ожидаемом роутером my_documents."""
    now = datetime.now(timezone.utc)
    doc = SimpleNamespace(
        id=doc_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        name="spec.docx",
        format="docx",
        size_bytes=1024,
        status="draft",
        current_analysis_job_id=None,
        created_at=now,
        updated_at=now,
    )
    return {
        "document": doc,
        "project_name": "Test Project",
        "suggestions_total": 3,
        "suggestions_pending": 1,
        "suggestions_accepted": 1,
        "suggestions_rejected": 1,
    }


@pytest.mark.asyncio
async def test_list_all_for_user_delegates_to_repo() -> None:
    """list_all_for_user вызывает репозиторий ровно один раз и возвращает его результат."""
    svc, repo = _make_service()
    owner_id = uuid.uuid4()

    items, total = await svc.list_all_for_user(owner_id, limit=10, offset=0)

    repo.list_all_for_user.assert_called_once()
    call_kwargs = repo.list_all_for_user.call_args
    assert call_kwargs.args[0] == owner_id
    assert total == 0
    assert items == []


@pytest.mark.asyncio
async def test_list_all_for_user_caps_limit() -> None:
    """Значение limit не должно превышать 200 на уровне сервиса."""
    svc, repo = _make_service()

    await svc.list_all_for_user(uuid.uuid4(), limit=9999, offset=0)

    call_kwargs = repo.list_all_for_user.call_args.kwargs
    assert call_kwargs["limit"] <= 200


@pytest.mark.asyncio
async def test_list_all_for_user_row_shape() -> None:
    """P1: row из репозитория содержит все ключи, ожидаемые роутером my_documents."""
    svc, repo = _make_service()
    row = _make_doc_row()
    repo.list_all_for_user.return_value = ([row], 1)

    items, total = await svc.list_all_for_user(uuid.uuid4(), limit=10, offset=0)

    assert total == 1
    assert len(items) == 1
    r = items[0]
    # Все ключи, которые my_documents.py читает из row:
    assert hasattr(r["document"], "id")
    assert hasattr(r["document"], "project_id")
    assert hasattr(r["document"], "name")
    assert hasattr(r["document"], "format")
    assert hasattr(r["document"], "size_bytes")
    assert hasattr(r["document"], "status")
    assert hasattr(r["document"], "created_at")
    assert hasattr(r["document"], "updated_at")
    assert "project_name" in r
    assert "suggestions_total" in r
    assert "suggestions_pending" in r
    assert "suggestions_accepted" in r
    assert "suggestions_rejected" in r
