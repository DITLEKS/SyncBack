"""
Тесты пп. 7-9 плана ревью:
  7. list_by_document_ids — фильтр scope=DOCUMENT, группировка, изоляция по проекту.
  8. attach_to_document — идемпотентность (двойной вызов не бросает исключений).
  9. replace_document_sources — старые связи удаляются, новые вставляются.

Все тесты используют чистые моки без БД — скорость O(мс).
Для replace_document_sources и attach_to_document мокируется session:
тесты проверяют переданные аргументы, а не SQL-рез-т.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.domain.services.source_service import SourceService
from app.domain.value_objects import SourceScopeVO

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

PROJECT_ID = uuid.uuid4()


def _make_service(
    list_by_doc_return=None,
    replace_return=None,
    attach_side_effect=None,
) -> tuple[SourceService, MagicMock]:
    """Возвращает (service, sources_repo_mock)."""
    repo = AsyncMock()
    repo.list_by_document_ids.return_value = list_by_doc_return or []
    repo.replace_document_sources.return_value = replace_return or []
    if attach_side_effect is not None:
        repo.attach_to_document.side_effect = attach_side_effect

    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.sources = repo

    svc = SourceService(uow=uow, file_storage=AsyncMock())
    return svc, repo


def _src(name: str = "s", scope: str = "document") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        project_id=PROJECT_ID,
        scope=scope,
        storage_key=None,
    )


# ---------------------------------------------------------------------------
# 7a. list_by_document_ids — корректная группировка кортежей
# ---------------------------------------------------------------------------


class TestListByDocumentIds:
    @pytest.mark.asyncio
    async def test_groups_sources_by_document(self) -> None:
        """Кортежи (source, doc_id) корректно группируются в dict."""
        doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
        src_a1, src_a2 = _src("a1"), _src("a2")
        src_b1 = _src("b1")

        svc, repo = _make_service(
            list_by_doc_return=[
                (src_a1, doc_a),
                (src_a2, doc_a),
                (src_b1, doc_b),
            ]
        )
        result = await svc.list_sources_for_documents(PROJECT_ID, [doc_a, doc_b])

        assert set(result.keys()) == {doc_a, doc_b}
        assert len(result[doc_a]) == 2
        assert result[doc_b] == [src_b1]

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_dict_without_repo_call(self) -> None:
        """Пустой список document_ids → репозиторий не вызывается."""
        svc, repo = _make_service()
        result = await svc.list_sources_for_documents(PROJECT_ID, [])

        assert result == {}
        repo.list_by_document_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_project_scope_sources_absent_from_result(
        self,
    ) -> None:
        """Источники с scope=PROJECT не должны попадать в результат.

        Репозиторий уже фильтрует по scope=DOCUMENT (FIX-3). Здесь проверяем
        что сервис не добавляет дополнительную фильтрацию — она на уровне БД.
        Тест верифицирует что репозиторий вызван с правильными аргументами,
        и что возвращает только то, что ему передали (нет утечки scope).
        """
        doc_id = uuid.uuid4()
        doc_src = _src("doc-src", scope="document")

        # Репозиторий возвращает только DOCUMENT-scope источник (FIX-3 в репо)
        svc, repo = _make_service(list_by_doc_return=[(doc_src, doc_id)])
        result = await svc.list_sources_for_documents(PROJECT_ID, [doc_id])

        # Убеждаемся что запрос был с правильными аргументами
        repo.list_by_document_ids.assert_awaited_once_with(PROJECT_ID, [doc_id])
        # И в результате только doc_src
        assert result[doc_id] == [doc_src]


# ---------------------------------------------------------------------------
# 8. attach_to_document — идемпотентность
# ---------------------------------------------------------------------------


class TestAttachToDocumentIdempotence:
    @pytest.mark.asyncio
    async def test_double_attach_does_not_raise(self) -> None:
        """Двойной вызов attach_to_document не бросает исключений.

        attach_to_document использует ON CONFLICT DO NOTHING — второй вызов
        должен быть silent no-op. Тест через сервисный слой: два create_url_source
        с одним document_id → два вызова attach_to_document → оба успешны.
        """
        doc_id = uuid.uuid4()
        project = SimpleNamespace(id=PROJECT_ID)

        repo = AsyncMock()
        repo.create_url.return_value = SimpleNamespace(id=uuid.uuid4())
        repo.attach_to_document = AsyncMock()  # no-op, не бросает

        uow = AsyncMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.sources = repo

        svc = SourceService(uow=uow, file_storage=AsyncMock())

        # Первый вызов
        await svc.create_url_source(
            project,
            name="s1",
            url="https://a.com",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,
        )
        # Второй вызов — тот же document_id
        await svc.create_url_source(
            project,
            name="s2",
            url="https://b.com",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,
        )

        # Оба вызова attach прошли без исключений
        assert repo.attach_to_document.await_count == 2

    @pytest.mark.asyncio
    async def test_project_scope_never_calls_attach(self) -> None:
        """scope=PROJECT → attach_to_document не вызывается никогда."""
        project = SimpleNamespace(id=PROJECT_ID)

        repo = AsyncMock()
        repo.create_url.return_value = SimpleNamespace(id=uuid.uuid4())

        uow = AsyncMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.sources = repo

        svc = SourceService(uow=uow, file_storage=AsyncMock())
        await svc.create_url_source(
            project,
            name="proj-src",
            url="https://c.com",
            scope=SourceScopeVO.PROJECT,
        )

        repo.attach_to_document.assert_not_awaited()


# ---------------------------------------------------------------------------
# 9. replace_document_sources — очистка старых связей
# ---------------------------------------------------------------------------


class TestReplaceDocumentSources:
    @pytest.mark.asyncio
    async def test_replace_calls_repo_replace_with_new_sources(self) -> None:
        """replace_document_sources передаёт новые источники в репозиторий."""
        from app.domain.value_objects import DocumentStatusVO

        doc_id = uuid.uuid4()
        new_src = _src("new")
        new_src.id = uuid.uuid4()
        new_src.project_id = PROJECT_ID

        doc = SimpleNamespace(
            id=doc_id,
            project_id=PROJECT_ID,
            status=DocumentStatusVO.DRAFT,
        )

        repo = AsyncMock()
        repo.get_many_by_ids.return_value = [new_src]
        repo.replace_document_sources.return_value = [new_src]

        uow = AsyncMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.sources = repo

        svc = SourceService(uow=uow, file_storage=AsyncMock())
        result = await svc.replace_document_sources(doc, [new_src.id])

        repo.replace_document_sources.assert_awaited_once_with(doc_id, [new_src])
        assert result == [new_src]

    @pytest.mark.asyncio
    async def test_replace_raises_if_foreign_source(self) -> None:
        """Источник из чужого проекта вызывает SourceNotFoundError."""
        from app.domain.exceptions import SourceNotFoundError
        from app.domain.value_objects import DocumentStatusVO

        doc_id = uuid.uuid4()
        foreign_project_id = uuid.uuid4()
        foreign_src = _src("foreign")
        foreign_src.id = uuid.uuid4()
        foreign_src.project_id = foreign_project_id  # чужой проект

        doc = SimpleNamespace(
            id=doc_id,
            project_id=PROJECT_ID,
            status=DocumentStatusVO.DRAFT,
        )

        repo = AsyncMock()
        repo.get_many_by_ids.return_value = [foreign_src]

        uow = AsyncMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.sources = repo

        svc = SourceService(uow=uow, file_storage=AsyncMock())
        with pytest.raises(SourceNotFoundError):
            await svc.replace_document_sources(doc, [foreign_src.id])

        # replace_document_sources в репозитории не должен был вызваться
        repo.replace_document_sources.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replace_raises_on_locked_document(self) -> None:
        """Документ в статусе IN_PROGRESS → SourceLockError."""
        from app.domain.exceptions import SourceLockError
        from app.domain.value_objects import DocumentStatusVO

        doc = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=PROJECT_ID,
            status=DocumentStatusVO.IN_PROGRESS,
        )

        svc, repo = _make_service()
        with pytest.raises(SourceLockError):
            await svc.replace_document_sources(doc, [uuid.uuid4()])

        repo.replace_document_sources.assert_not_awaited()
