"""
F-2: Contract-тесты роутера auth.

Покрываемые HTTP-контракты:
  POST /api/v1/auth/register  — 201 | 409
  POST /api/v1/auth/login     — 200 | 401
  GET  /health                — 200 (smoke, без auth)

Тесты работают через мок сервисного слоя — реальная БД не нужна.
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
# Фикстуры
# ---------------------------------------------------------------------------

USER_ID = uuid.uuid4()


def _make_user(
    email: str = "alice@example.com",
    username: str = "alice",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID,
        email=email,
        username=username,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


def _make_token() -> SimpleNamespace:
    return SimpleNamespace(access_token="tok.test.jwt", token_type="bearer")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# GET /health — smoke-тест, не требует auth
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_register_returns_201():
    """Успешная регистрация возвращает 201 и access_token."""
    from app.domain.services.auth_service import AuthService

    svc = AsyncMock(spec=AuthService)
    svc.register.return_value = (_make_user(), _make_token())

    with patch("app.core.dependencies.get_auth_service", return_value=svc):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/auth/register",
                json={"email": "alice@example.com", "username": "alice", "password": "S3cur3!"},
            )
    assert r.status_code == 201
    body = r.json()
    assert "access_token" in body


@pytest.mark.anyio
async def test_register_duplicate_returns_409():
    """Повторная регистрация — 409 Conflict."""
    from app.domain.exceptions import UserAlreadyExistsError
    from app.domain.services.auth_service import AuthService

    svc = AsyncMock(spec=AuthService)
    svc.register.side_effect = UserAlreadyExistsError("Пользователь уже существует")

    with patch("app.core.dependencies.get_auth_service", return_value=svc):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/auth/register",
                json={"email": "alice@example.com", "username": "alice", "password": "S3cur3!"},
            )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_login_returns_200_with_token():
    """Успешный логин возвращает 200 и access_token."""
    from app.domain.services.auth_service import AuthService

    svc = AsyncMock(spec=AuthService)
    svc.login.return_value = _make_token()

    with patch("app.core.dependencies.get_auth_service", return_value=svc):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/auth/login",
                json={"email": "alice@example.com", "password": "S3cur3!"},
            )
    assert r.status_code == 200
    assert "access_token" in r.json()


@pytest.mark.anyio
async def test_login_wrong_password_returns_401():
    """Неверный пароль — 401."""
    from app.domain.exceptions import InvalidCredentialsError
    from app.domain.services.auth_service import AuthService

    svc = AsyncMock(spec=AuthService)
    svc.login.side_effect = InvalidCredentialsError("Неверные учетные данные")

    with patch("app.core.dependencies.get_auth_service", return_value=svc):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/auth/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
    assert r.status_code == 401
