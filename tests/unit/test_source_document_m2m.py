"""
WARN-2: scope=document + document_id — M2M-запись в document_sources.

Баги BUG-A и BUG-B закрыты — xfail-отметки сняты.
Тесты верифицируют корректное поведение после исправления.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

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
    sources_repo.attach_to_document = AsyncMock()  # M2M-вставка

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
# BUG-A: сервисные методы принимают document_id
# ---------------------------------------------------------------------------

class TestServiceSignatures:
    """create_url/note/file_source имеют document_id: uuid.UUID | None = None."""

    def test_create_url_source_accepts_document_id(self) -> None:
        sig = inspect.signature(SourceService.create_url_source)
        assert "document_id" in sig.parameters

    def test_create_note_source_accepts_document_id(self) -> None:
        sig = inspect.signature(SourceService.create_note_source)
        assert "document_id" in sig.parameters

    def test_create_file_source_accepts_document_id(self) -> None:
        sig = inspect.signature(SourceService.create_file_source)
        assert "document_id" in sig.parameters


# ---------------------------------------------------------------------------
# BUG-A: M2M-вставка происходит при scope=document
# ---------------------------------------------------------------------------

class TestM2MInsert:
    """При scope=DOCUMENT + document_id вызывается attach_to_document."""

    @pytest.mark.asyncio
    async def test_url_source_with_document_id_creates_m2m(self) -> None:
        service, uow, sources_repo = _make_service()
        project = _make_project()
        doc_id = uuid.uuid4()

        await service.create_url_source(
            project,
            name="Test URL",
            url="https://example.com",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,
        )

        sources_repo.attach_to_document.assert_awaited_once()
        call_args = sources_repo.attach_to_document.call_args
        passed_doc_id = call_args.args[1] if len(call_args.args) >= 2 else call_args.kwargs.get("document_id")
        assert passed_doc_id == doc_id

    @pytest.mark.asyncio
    async def test_file_source_with_document_id_creates_m2m(self) -> None:
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
            document_id=doc_id,
        )

        sources_repo.attach_to_document.assert_awaited_once()
        call_args = sources_repo.attach_to_document.call_args
        passed_doc_id = call_args.args[1] if len(call_args.args) >= 2 else call_args.kwargs.get("document_id")
        assert passed_doc_id == doc_id

    @pytest.mark.asyncio
    async def test_note_source_with_document_id_creates_m2m(self) -> None:
        service, uow, sources_repo = _make_service()
        project = _make_project()
        doc_id = uuid.uuid4()

        await service.create_note_source(
            project,
            name="Meeting notes",
            text_content="Short note",
            scope=SourceScopeVO.DOCUMENT,
            document_id=doc_id,
        )

        sources_repo.attach_to_document.assert_awaited_once()
        call_args = sources_repo.attach_to_document.call_args
        passed_doc_id = call_args.args[1] if len(call_args.args) >= 2 else call_args.kwargs.get("document_id")
        assert passed_doc_id == doc_id


# ---------------------------------------------------------------------------
# BUG-B: роутер передаёт document_id в сервис
# ---------------------------------------------------------------------------

class TestRouterWiring:
    """create_url_source и create_note_source в роутере передают document_id."""

    def test_router_create_url_passes_document_id(self) -> None:
        import ast
        from pathlib import Path

        router_path = (
            Path(__file__).parent.parent.parent
            / "app" / "api" / "v1" / "routers" / "sources.py"
        )
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
        url_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_url_source"
        ]
        assert url_calls
        for call_node in url_calls:
            kw_names = {kw.arg for kw in call_node.keywords}
            assert "document_id" in kw_names, f"create_url_source не передаёт document_id. kwargs={kw_names}"

    def test_router_create_note_passes_document_id(self) -> None:
        import ast
        from pathlib import Path

        router_path = (
            Path(__file__).parent.parent.parent
            / "app" / "api" / "v1" / "routers" / "sources.py"
        )
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
        note_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_note_source"
        ]
        assert note_calls
        for call_node in note_calls:
            kw_names = {kw.arg for kw in call_node.keywords}
            assert "document_id" in kw_names, f"create_note_source не передаёт document_id. kwargs={kw_names}"


# ---------------------------------------------------------------------------
# Smoke: scope=project без document_id — нет регрессии
# ---------------------------------------------------------------------------

class TestProjectScopeUnaffected:
    """scope=project без document_id не затронут исправлениями."""

    @pytest.mark.asyncio
    async def test_create_url_source_project_scope_no_m2m(self) -> None:
        service, uow, sources_repo = _make_service()
        project = _make_project()

        result = await service.create_url_source(
            project, name="Docs", url="https://docs.example.com",
            scope=SourceScopeVO.PROJECT,
        )

        sources_repo.attach_to_document.assert_not_awaited()
        sources_repo.create_url.assert_awaited_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_file_source_project_scope_no_m2m(self) -> None:
        service, uow, sources_repo = _make_service()
        project = _make_project()

        result = await service.create_file_source(
            project, name="manual.pdf", filename="manual.pdf",
            content=b"data", content_type="application/pdf",
            scope=SourceScopeVO.PROJECT,
        )

        sources_repo.attach_to_document.assert_not_awaited()
        sources_repo.create_with_id.assert_awaited_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_url_source_document_scope_no_doc_id_no_m2m(self) -> None:
        """scope=DOCUMENT без document_id — M2M не вызывается."""
        service, uow, sources_repo = _make_service()
        project = _make_project()

        result = await service.create_url_source(
            project, name="Ref", url="https://ref.example.com",
            scope=SourceScopeVO.DOCUMENT,
            # document_id намеренно не передаём
        )

        sources_repo.attach_to_document.assert_not_awaited()
        assert result is not None
