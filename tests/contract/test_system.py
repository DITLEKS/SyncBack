"""Служебные эндпоинты: диагностика LLM только для администратора."""

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.user import User
from tests.contract.conftest import register_and_login


async def _make_admin(sessionmaker: async_sessionmaker[AsyncSession], email: str) -> None:
    async with sessionmaker() as session:
        await session.execute(update(User).where(User.email == email).values(role=UserRole.ADMIN))
        await session.commit()
        assert (
            await session.scalar(select(User.role).where(User.email == email))
        ) == UserRole.ADMIN


async def test_llm_health_requires_admin(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    assert (await client.get("/api/v1/system/llm-health")).status_code == 401

    headers = await register_and_login(client, "plain@example.com")
    response = await client.get("/api/v1/system/llm-health", headers=headers)
    assert response.status_code == 403, response.text

    headers = await register_and_login(client, "admin@example.com")
    await _make_admin(sessionmaker, "admin@example.com")
    response = await client.get("/api/v1/system/llm-health", headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"provider", "healthy"}


async def test_capabilities_is_public(client: AsyncClient) -> None:
    response = await client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    assert "upload" in response.json()
