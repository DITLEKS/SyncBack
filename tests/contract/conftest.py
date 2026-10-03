"""Контрактные тесты на реальном стеке: FastAPI → сервисы → репозитории → SQLite.

Сервисы не мокаются — проверяется, что слои согласованы между собой. Внешние
системы заменены: Redis на fakeredis, БД на SQLite в памяти.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.infrastructure.db.models  # noqa: F401
from app.core.dependencies import get_login_rate_limiter, get_refresh_token_store, get_settings
from app.core.limiter import limiter
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_db_session
from app.infrastructure.security.login_rate_limiter import LoginRateLimiter
from app.infrastructure.security.refresh_token_store import RefreshTokenStore
from app.main import app


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    redis = fakeredis.aioredis.FakeRedis()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    limiter.reset()  # лимиты /auth/* считаются по IP и иначе копятся между тестами
    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_login_rate_limiter] = lambda: LoginRateLimiter(
        redis, get_settings()
    )
    app.dependency_overrides[get_refresh_token_store] = lambda: RefreshTokenStore(redis)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        await redis.aclose()


async def register_and_login(
    client: AsyncClient, email: str = "user@example.com"
) -> dict[str, str]:
    """Зарегистрировать пользователя и вернуть заголовок Authorization."""
    password = "Correct-Horse-Battery-9"
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
