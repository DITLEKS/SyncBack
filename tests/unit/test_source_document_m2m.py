"""
WARN-2: scope=document + document_id — M2M-запись в document_sources.

Фиксируем два связанных бага, которые превентируют создание
связи источник-документ при scope=document:

  BUG-A (сервис): create_url_source / create_note_source / create_file_source
    не принимают аргумент document_id — M2M-запись в document_sources
    не вставляется даже при scope=document.

  BUG-B (роутер): create_url_source и create_note_source передают scope,
    но не передают payload.document_id в сервис.

Тесты помечены @pytest.mark.xfail(strict=True) до закрытия багов.
При исправлении — убрать @pytest.mark.xfail.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.domain.services.source_service import SourceService
from app.domain.value_objects import SourceScopeVO


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _make_service() -> tuple[SourceService, MagicMock, MagicMock]:
    """Возвращает (service, uow_mock, sources_repo_mock)."""
    sources_repo = MagicMock()
    sources_repo.create_url = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    sources_repo.create_with_id = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    sources_repo.attach_to_document = AsyncMock()  # М2M-вставка

    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    uow.sources = sources_repo

    storage = MagicMock()
    storage.upload = AsyncMock()

    settings = MagicMock()
    settings.max_upload_size_bytes = 10 * 1024 * 1024
    settings.max_upload_size_mb = 10

    service = SourceService(uow=uow, file_storage=storage, settings=settings)
    return service, uow, sources_repo


def _make_project(project_id: uuid.UUID | None = None) -> MagicMock:
    p = MagicMock()
    p.id = project_id or uuid.uuid4()
    return p


# ---------------------------------------------------------------------------
# BUG-A: create_url_source не принимает document_id
# ---------------------------------------------------------------------------

class TestBugAServiceSignatures:
    """Сервисные методы должны принимать document_id: uuid.UUID | None."""

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-A: create_url_source сейчас не принимает document_id",
    )
    def test_create_url_source_accepts_document_id(self) -> None:
        """create_url_source(project, name, url, scope, document_id=...) — должен работать."""
        sig = inspect.signature(SourceService.create_url_source)
        assert "document_id" in sig.parameters, (
            "create_url_source должен иметь параметр document_id: uuid.UUID | None = None"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-A: create_note_source сейчас не принимает document_id",
    )
    def test_create_note_source_accepts_document_id(self) -> None:
        """create_note_source(project, name, text, scope, document_id=...) — должен работать."""
        sig = inspect.signature(SourceService.create_note_source)
        assert "document_id" in sig.parameters, (
            "create_note_source должен иметь параметр document_id: uuid.UUID | None = None"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-A: create_file_source сейчас не принимает document_id",
    )
    def test_create_file_source_accepts_document_id(self) -> None:
        """create_file_source(project, name, filename, content, ct, scope, document_id=...) — должен работать."""
        sig = inspect.signature(SourceService.create_file_source)
        assert "document_id" in sig.parameters, (
            "create_file_source должен иметь параметр document_id: uuid.UUID | None = None"
        )


# ---------------------------------------------------------------------------
# BUG-A: M2M-вставка должна происходить при scope=document
# ---------------------------------------------------------------------------

class TestBugAM2MInsert:
    """create_url_source(scope=DOCUMENT, document_id=X) — M2M-вставка в document_sources."""

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-A: create_url_source не вставляет M2M-запись",
    )
    @pytest.mark.asyncio
    async def test_url_source_with_document_id_creates_m2m(self) -> None:
        """M2M-запись в document_sources вставляется при scope=document."""
        service, uow, sources_repo = _make_service()
        project = _make_project()
        doc_id = uuid.uuid4()

        # Вызов должен успешно пройти с document_id
        await service.create_url_source(
            project,
            name="Test URL",
            url="https://example.com",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,  # type: ignore[call-arg]  # ожидаем баг
        )

        # Репозиторий должен был вызван с document_id для M2M
        sources_repo.attach_to_document.assert_awaited_once()
        call_args = sources_repo.attach_to_document.call_args
        assert call_args.kwargs.get("document_id") == doc_id or (
            len(call_args.args) >= 2 and call_args.args[1] == doc_id
        ), f"attach_to_document не принял document_id={doc_id}"

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-A: create_file_source не вставляет M2M-запись",
    )
    @pytest.mark.asyncio
    async def test_file_source_with_document_id_creates_m2m(self) -> None:
        """M2M-запись в document_sources вставляется при scope=document."""
        service, uow, sources_repo = _make_service()
        project = _make_project()
        doc_id = uuid.uuid4()

        await service.create_file_source(
            project,
            name="report.pdf",
            filename="report.pdf",
            content=b"PDF content",
            content_type="application/pdf",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,  # type: ignore[call-arg]
        )

        sources_repo.attach_to_document.assert_awaited_once()
        call_args = sources_repo.attach_to_document.call_args
        assert call_args.kwargs.get("document_id") == doc_id or (
            len(call_args.args) >= 2 and call_args.args[1] == doc_id
        ), f"attach_to_document не принял document_id={doc_id}"


# ---------------------------------------------------------------------------
# BUG-B: роутер не передаёт document_id в сервис
# ---------------------------------------------------------------------------

class TestBugBRouterWiring:
    """create_url_source в роутере должен передавать payload.document_id."""

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-B: роутер sources.py не передаёт document_id в source_service.create_url_source",
    )
    def test_router_create_url_passes_document_id(self) -> None:
        """create_url_source в роутере вызывается с document_id=payload.document_id."""
        import ast
        import textwrap
        from pathlib import Path

        router_path = (
            Path(__file__).parent.parent.parent
            / "app" / "api" / "v1" / "routers" / "sources.py"
        )
        source_code = router_path.read_text(encoding="utf-8")
        tree = ast.parse(source_code)

        # Ищем вызов create_url_source(...) в AST
        url_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_url_source"
        ]
        assert url_calls, "create_url_source не найден в роутере"

        for call_node in url_calls:
            kw_names = {kw.arg for kw in call_node.keywords}
            assert "document_id" in kw_names, (
                f"Вызов create_url_source в {router_path.name} "
                f"не передаёт document_id. "
                f"Найденные kwargs: {kw_names}"
            )

    @pytest.mark.xfail(
        strict=True,
        reason="BUG-B: роутер sources.py не передаёт document_id в source_service.create_note_source",
    )
    def test_router_create_note_passes_document_id(self) -> None:
        """create_note_source в роутере вызывается с document_id=payload.document_id."""
        import ast
        from pathlib import Path

        router_path = (
            Path(__file__).parent.parent.parent
            / "app" / "api" / "v1" / "routers" / "sources.py"
        )
        source_code = router_path.read_text(encoding="utf-8")
        tree = ast.parse(source_code)

        note_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_note_source"
        ]
        assert note_calls, "create_note_source не найден в роутере"

        for call_node in note_calls:
            kw_names = {kw.arg for kw in call_node.keywords}
            assert "document_id" in kw_names, (
                f"Вызов create_note_source в {router_path.name} "
                f"не передаёт document_id. "
                f"Найденные kwargs: {kw_names}"
            )


# ---------------------------------------------------------------------------
# Smoke: scope=project без document_id всё ещё работает (нерегрессия)
# ---------------------------------------------------------------------------

class TestProjectScopeUnaffected:
    """scope=project без document_id не затронут исправлениями."""

    @pytest.mark.asyncio
    async def test_create_url_source_project_scope_works(self) -> None:
        """create_url_source(scope=PROJECT) работает без document_id."""
        service, uow, sources_repo = _make_service()
        project = _make_project()

        result = await service.create_url_source(
            project,
            name="Docs",
            url="https://docs.example.com",
            scope=SourceScopeVO.PROJECT,
        )

        sources_repo.create_url.assert_awaited_once()
        uow.commit.assert_awaited_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_file_source_project_scope_works(self) -> None:
        """create_file_source(scope=PROJECT) работает без document_id."""
        service, uow, sources_repo = _make_service()
        project = _make_project()

        result = await service.create_file_source(
            project,
            name="manual.pdf",
            filename="manual.pdf",
            content=b"data",
            content_type="application/pdf",
            scope=SourceScopeVO.PROJECT,
        )

        sources_repo.create_with_id.assert_awaited_once()
        uow.commit.assert_awaited_once()
        assert result is not None
