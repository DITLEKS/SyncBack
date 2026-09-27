"""
F-2: Contract-тесты роутеров suggestions и editor.

Покрываемые HTTP-контракты:
  GET  /api/v1/projects/{pid}/documents/{did}/suggestions        — 200 list
  POST /api/v1/projects/{pid}/documents/{did}/suggestions/{sid}/accept  — 200
  POST /api/v1/projects/{pid}/documents/{did}/suggestions/{sid}/reject  — 200
  POST …/accept неизвестного suggestion                              — 404
  GET  /api/v1/projects/{pid}/documents/{did}/editor             — 200
  POST /api/v1/projects/{pid}/documents/{did}/editor/finalize    — 200 | 409

Все тесты работают через мок — реальная БД не нужна.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app

# ---------------------------------------------------------------------------
# Общие фикстуры
# ---------------------------------------------------------------------------

USER_ID     = uuid.uuid4()
PROJECT_ID  = uuid.uuid4()
DOC_ID      = uuid.uuid4()
SUGG_ID     = uuid.uuid4()

AUTH_HEADERS = {"Authorization": "Bearer fake.jwt.token"}


def _make_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID, email="carol@example.com", username="carol",
        is_active=True, created_at=datetime.now(timezone.utc),
    )


def _make_project() -> SimpleNamespace:
    return SimpleNamespace(
        id=PROJECT_ID, name="Docs project", owner_id=USER_ID,
        created_at=datetime.now(timezone.utc),
    )


def _make_suggestion(
    sid: uuid.UUID = SUGG_ID,
    status: str = "pending",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sid,
        document_id=DOC_ID,
        type="update",
        section="Introduction",
        original_text="Old text.",
        suggested_text="New text.",
        reason="Source updated.",
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def _make_editor_state() -> SimpleNamespace:
    return SimpleNamespace(
        document_id=DOC_ID,
        content="# Doc\nContent here.",
        review_version=1,
        pending_suggestions_count=2,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _patch_user():
    return patch("app.api.deps.get_current_user", return_value=_make_user())


def _patch_project():
    return patch("app.api.deps.get_allowed_project", return_value=_make_project())


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_suggestions_returns_200():
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.list_for_document.return_value = [_make_suggestion()]

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert data[0]["id"] == str(SUGG_ID)


@pytest.mark.anyio
async def test_accept_suggestion_returns_200():
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.accept.return_value = _make_suggestion(status="accepted")

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions/{SUGG_ID}/accept",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"


@pytest.mark.anyio
async def test_reject_suggestion_returns_200():
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.reject.return_value = _make_suggestion(status="rejected")

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions/{SUGG_ID}/reject",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


@pytest.mark.anyio
async def test_accept_unknown_suggestion_returns_404():
    from app.domain.exceptions import SuggestionNotFoundError
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.accept.side_effect = SuggestionNotFoundError("Предложение не найдено")

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions/{uuid.uuid4()}/accept",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_editor_state_returns_200():
    from app.domain.services.editor_service import EditorService

    svc = AsyncMock(spec=EditorService)
    svc.get_state.return_value = _make_editor_state()

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_editor_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/editor",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    body = r.json()
    assert "review_version" in body
    assert "pending_suggestions_count" in body


@pytest.mark.anyio
async def test_finalize_review_happy_path_returns_200():
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.finalize_review_versioned.return_value = SimpleNamespace(
        document_id=DOC_ID,
        review_version=2,
        accepted=3,
        rejected=1,
    )

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions/finalize",
                json={"review_version": 1},
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    assert r.json()["review_version"] == 2


@pytest.mark.anyio
async def test_finalize_review_stale_version_returns_409():
    """PUT /finalize с устаревшей версией → 409."""
    from app.domain.exceptions import StaleReviewVersionError
    from app.domain.services.suggestion_service import SuggestionService

    svc = AsyncMock(spec=SuggestionService)
    svc.finalize_review_versioned.side_effect = StaleReviewVersionError(
        "Конфликт версий: ожидалась 2, пришло 1"
    )

    with (
        _patch_user(),
        _patch_project(),
        patch("app.core.dependencies.get_suggestion_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}/suggestions/finalize",
                json={"review_version": 1},
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 409
