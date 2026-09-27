"""
P1: юнит-тесты SourceService.list_sources_for_documents.

Покрываемые сценарии:
  1. batch_returns_grouped_by_document_id
     — репозиторий возвращает N источников, сервис группирует по document_id.
  2. empty_document_ids_skips_repo
     — при пустом списке document_ids репозиторий не вызывается, возвращается {}.
  3. document_with_no_sources_absent_from_result
     — если у документа нет источников, его ключ отсутствует в словаре
       (роутер использует .get(id, []) — это ожидаемое поведение).
  4. multiple_sources_per_document
     — несколько источников на один документ корректно накапливаются в список.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.source_service import SourceService

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

PROJECT_ID = uuid.uuid4()


def _make_service(repo_sources: list) -> SourceService:
    """Создаёт SourceService с замоканным UoW."""
    repo = AsyncMock()
    repo.list_by_document_ids.return_value = repo_sources

    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.sources = repo

    storage = AsyncMock()
    return SourceService(uow=uow, file_storage=storage)


def _src(document_id: uuid.UUID, name: str = "wiki") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        type="url",
        document_id=document_id,
        project_id=PROJECT_ID,
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_returns_grouped_by_document_id() -> None:
    """Результат группируется по document_id."""
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    src_a = _src(doc_a, "gdoc")
    src_b = _src(doc_b, "confluence")

    svc = _make_service([src_a, src_b])
    result = await svc.list_sources_for_documents(PROJECT_ID, [doc_a, doc_b])

    assert set(result.keys()) == {doc_a, doc_b}
    assert result[doc_a] == [src_a]
    assert result[doc_b] == [src_b]


@pytest.mark.asyncio
async def test_empty_document_ids_skips_repo() -> None:
    """При пустом списке репозиторий не вызывается."""
    repo = AsyncMock()
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.sources = repo

    svc = SourceService(uow=uow, file_storage=AsyncMock())
    result = await svc.list_sources_for_documents(PROJECT_ID, [])

    assert result == {}
    repo.list_by_document_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_with_no_sources_absent_from_result() -> None:
    """Документ без источников не попадает в словарь — роутер использует .get(id, [])."""
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    src_a = _src(doc_a)

    svc = _make_service([src_a])  # doc_b не представлен
    result = await svc.list_sources_for_documents(PROJECT_ID, [doc_a, doc_b])

    assert doc_a in result
    assert doc_b not in result
    assert result.get(doc_b, []) == []


@pytest.mark.asyncio
async def test_multiple_sources_per_document() -> None:
    """Несколько источников одного документа корректно накапливаются."""
    doc_a = uuid.uuid4()
    src1 = _src(doc_a, "source-1")
    src2 = _src(doc_a, "source-2")
    src3 = _src(doc_a, "source-3")

    svc = _make_service([src1, src2, src3])
    result = await svc.list_sources_for_documents(PROJECT_ID, [doc_a])

    assert len(result[doc_a]) == 3
    assert {s.name for s in result[doc_a]} == {"source-1", "source-2", "source-3"}
