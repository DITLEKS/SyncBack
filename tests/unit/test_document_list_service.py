"""
Unit-тесты для DocumentService.list_all_for_user (P0-4).

Проверяют два инварианта:
1. Метод делегирует вызов репозиторию и возвращает его результат без изменений.
2. Значение limit ограничивается 200 вне зависимости от переданного значения.
"""

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.services.document_service import DocumentService


def _make_service() -> tuple[DocumentService, AsyncMock]:
    """Создаёт DocumentService с замоканным репозиторием и заглушками зависимостей."""
    repo = AsyncMock()
    repo.list_all_for_user.return_value = ([], 0)

    storage = AsyncMock()
    svc = DocumentService(
        document_repository=repo,
        file_storage=storage,
    )
    return svc, repo


@pytest.mark.asyncio
async def test_list_all_for_user_delegates_to_repo() -> None:
    """list_all_for_user вызывает репозиторий ровно один раз и возвращает его результат."""
    svc, repo = _make_service()
    owner_id = uuid.uuid4()

    items, total = await svc.list_all_for_user(owner_id, limit=10, offset=0)

    repo.list_all_for_user.assert_called_once()
    call_kwargs = repo.list_all_for_user.call_args

    # Первый позиционный аргумент — owner_id
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
