"""
I-1: Юнит-тесты роутера GET /api/v1/documents (my_documents).

Проверяем, что sources в DocumentListItem:
  1. happy_path        — источники есть у первого документа, у второго пусто.
  2. no_sources        — батч возвращает {}, у всех documents sources == [].
  3. two_projects      — документы из двух проектов → два батч-вызова,
                         результаты сливаются корректно.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

USER_ID = uuid.uuid4()
PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
DOC_1 = uuid.uuid4()
DOC_2 = uuid.uuid4()
DOC_3 = uuid.uuid4()


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID,
        email="alice@example.com",
        username="alice",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


def _doc(
    doc_id: uuid.UUID = DOC_1,
    project_id: uuid.UUID = PROJECT_A,
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=doc_id,
        project_id=project_id,
        name="spec.docx",
        format="docx",
        size_bytes=1024,
        status="draft",
        current_analysis_job_id=None,
        created_at=now,
        updated_at=now,
    )


def _row(doc: SimpleNamespace, project_name: str = "Proj") -> dict:
    return {
        "document": doc,
        "project_name": project_name,
        "suggestions_total": 0,
        "suggestions_pending": 0,
        "suggestions_accepted": 0,
        "suggestions_rejected": 0,
    }


def _source(name: str = "wiki", src_type: str = "url") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        type=src_type,  # строка, не Enum
    )


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_current_user():
    with patch("app.api.deps.get_current_user", return_value=_user()):
        yield


@pytest.fixture()
def mock_doc_svc():
    svc = AsyncMock()
    with patch(
        "app.core.dependencies.get_document_service",
        return_value=svc,
    ):
        yield svc


@pytest.fixture()
def mock_src_svc():
    svc = AsyncMock()
    with patch(
        "app.core.dependencies.get_source_service",
        return_value=svc,
    ):
        yield svc


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_happy_path(mock_current_user, mock_doc_svc, mock_src_svc):
    """Первый документ имеет 1 источник, второй — 0."""
    doc1 = _doc(DOC_1, PROJECT_A)
    doc2 = _doc(DOC_2, PROJECT_A)
    mock_doc_svc.list_all_for_user.return_value = (
        [_row(doc1), _row(doc2)],
        2,
    )
    mock_src_svc.list_sources_for_documents.return_value = {
        DOC_1: [_source("wiki")],
        DOC_2: [],
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/api/v1/documents")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items[0]["sources"]) == 1
    assert items[0]["sources"][0]["name"] == "wiki"
    assert items[1]["sources"] == []

    # Один батч-запрос, оба document_id переданы вместе
    mock_src_svc.list_sources_for_documents.assert_awaited_once_with(
        PROJECT_A, [DOC_1, DOC_2]
    )


@pytest.mark.asyncio
async def test_sources_no_sources(mock_current_user, mock_doc_svc, mock_src_svc):
    """Батч возвращает пустой dict → sources == [] у всех документов."""
    doc1 = _doc(DOC_1, PROJECT_A)
    mock_doc_svc.list_all_for_user.return_value = ([_row(doc1)], 1)
    mock_src_svc.list_sources_for_documents.return_value = {}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/api/v1/documents")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["sources"] == []


@pytest.mark.asyncio
async def test_sources_two_projects(mock_current_user, mock_doc_svc, mock_src_svc):
    """Документы из двух проектов → два раздельных вызова list_sources_for_documents."""
    doc1 = _doc(DOC_1, PROJECT_A)
    doc2 = _doc(DOC_2, PROJECT_B)  # другой проект
    doc3 = _doc(DOC_3, PROJECT_B)
    mock_doc_svc.list_all_for_user.return_value = (
        [_row(doc1, "Proj A"), _row(doc2, "Proj B"), _row(doc3, "Proj B")],
        3,
    )

    # Настраиваем side_effect: разные ответы по project_id
    async def _batch_side_effect(project_id, doc_ids):
        if project_id == PROJECT_A:
            return {DOC_1: [_source("gdoc")]}
        return {DOC_2: [_source("confluence")], DOC_3: []}

    mock_src_svc.list_sources_for_documents.side_effect = _batch_side_effect

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/api/v1/documents")

    assert resp.status_code == 200
    items = resp.json()["items"]

    # DOC_1 (PROJECT_A) — 1 источник
    assert len(items[0]["sources"]) == 1
    assert items[0]["sources"][0]["name"] == "gdoc"

    # DOC_2 (PROJECT_B) — 1 источник
    assert len(items[1]["sources"]) == 1
    assert items[1]["sources"][0]["name"] == "confluence"

    # DOC_3 (PROJECT_B) — 0 источников
    assert items[2]["sources"] == []

    # Ровно 2 вызова батча
    assert mock_src_svc.list_sources_for_documents.await_count == 2
