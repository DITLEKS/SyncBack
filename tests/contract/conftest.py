"""Контрактные тесты на реальном стеке: FastAPI → сервисы → репозитории → SQLite.

Сервисы не мокаются — проверяется, что слои согласованы между собой. Внешние
системы заменены: Redis на fakeredis, БД на SQLite в памяти.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.dependencies as dependencies
import app.infrastructure.db.models  # noqa: F401
from app.domain.interfaces.file_storage import UploadContent
from app.domain.value_objects import DocumentStatusVO
from app.core.dependencies import get_login_rate_limiter, get_refresh_token_store, get_settings
from app.core.limiter import limiter
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.session import get_db_session
from app.infrastructure.security.login_rate_limiter import LoginRateLimiter
from app.infrastructure.security.refresh_token_store import RefreshTokenStore
from app.main import app


class InMemoryFileStorage:
    """Замена MinIO для контрактных тестов: файлы живут в словаре."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def upload(self, key: str, content: UploadContent) -> None:
        data = content.stream.read()
        assert len(data) == content.size
        self.files[key] = data

    async def download(self, key: str) -> bytes:
        return self.files[key]

    async def get_presigned_url(
        self, key: str, expires_in: int, download_name: str | None = None
    ) -> str:
        return f"http://storage.test/{key}?expires={expires_in}&name={download_name}"

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.files


@pytest.fixture
def file_storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryFileStorage:
    storage = InMemoryFileStorage()
    monkeypatch.setattr(dependencies, "_get_minio_storage", lambda: storage)
    return storage


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
async def client(
    sessionmaker: async_sessionmaker[AsyncSession], file_storage: InMemoryFileStorage
) -> AsyncIterator[AsyncClient]:
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


async def create_project(client: AsyncClient, headers: dict[str, str], name: str = "Проект") -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def upload_document(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    filename: str = "spec.txt",
    content: bytes = b"Hello world\n",
    content_type: str = "text/plain",
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, content, content_type)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def set_document_status(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: str, status: DocumentStatusVO
) -> None:
    """Перевести документ в нужный статус напрямую в БД, минуя жизненный цикл."""
    async with sessionmaker() as session:
        document = await session.get(Document, uuid.UUID(document_id))
        assert document is not None
        document.status = DocumentStatus(status.value)
        await session.commit()
