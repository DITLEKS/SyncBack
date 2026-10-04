"""Фикстуры интеграционных тестов: реальные PostgreSQL, Redis и S3-хранилище.

Схема БД должна быть накатана миграциями (`alembic upgrade head`), адреса сервисов
берутся из настроек приложения (.env или переменные окружения).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.infrastructure.cache.redis_client as redis_client_module
from app.core.config import get_settings
from app.core.limiter import limiter
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import (
    AnalysisJobStatus,
    ChangeType,
    DocumentStatus,
    SuggestionStatus,
)
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.session import get_db_session
from app.infrastructure.storage.s3_storage import S3FileStorage
from app.main import app

PASSWORD = "Correct-Horse-Battery-9"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Всё в tests/integration требует реальных Postgres/Redis/S3-хранилища.

    Маркер ставится централизованно, чтобы забытый pytestmark в отдельном файле
    не затягивал эти тесты в unit-прогон (там они зависают на подключении к БД).
    """
    integration_dir = Path(__file__).parent
    for item in items:
        if integration_dir in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    # pytest-asyncio создаёт event loop на каждый тест, а соединения asyncpg привязаны
    # к loop, поэтому движок живёт ровно один тест и без пула.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def pg_sessionmaker(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def db_session(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with pg_sessionmaker() as session:
        yield session


@pytest.fixture
def s3_storage() -> S3FileStorage:
    return S3FileStorage()


@pytest_asyncio.fixture(autouse=True)
async def _isolated_redis_client() -> AsyncIterator[None]:
    """Пересоздаёт глобальный Redis-клиент на event loop текущего теста.

    Клиент — синглтон процесса, а его соединения привязаны к loop, на котором
    созданы. В приложении loop один на весь процесс, в тестах — свой на каждый тест.
    """
    redis_client_module._redis_client = None
    yield
    client = redis_client_module._redis_client
    if client is not None:
        await client.aclose()
    redis_client_module._redis_client = None


@pytest_asyncio.fixture
async def api_client(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент к приложению, которое работает с реальными БД, Redis и хранилищем."""

    async def _session() -> AsyncIterator[AsyncSession]:
        async with pg_sessionmaker() as session:
            yield session

    limiter.reset()
    app.dependency_overrides[get_db_session] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def register_and_login(client: AsyncClient) -> dict[str, str]:
    """Зарегистрировать нового пользователя и вернуть заголовок Authorization."""
    email = f"it-{uuid.uuid4().hex}@example.com"
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_project(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": f"it-{uuid.uuid4().hex[:8]}"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def upload_document(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    filename: str,
    content: bytes,
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def seed_review(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: uuid.UUID, count: int
) -> list[uuid.UUID]:
    """Завершённый анализ с count pending-правками; документ — в awaiting_approval.

    Воркер в интеграционных тестах не запускается, поэтому результат анализа
    записывается напрямую.
    """
    async with sessionmaker() as session:
        job = AnalysisJob(document_id=document_id, status=AnalysisJobStatus.SUCCESS)
        session.add(job)
        await session.flush()
        suggestions = [
            Suggestion(
                analysis_job_id=job.id,
                document_id=document_id,
                section_ref=f"p{i}",
                change_type=ChangeType.MODIFY,
                status=SuggestionStatus.PENDING,
                original_text=f"old {i}",
                suggested_text=f"new {i}",
            )
            for i in range(count)
        ]
        session.add_all(suggestions)
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.AWAITING_APPROVAL, current_analysis_job_id=job.id)
        )
        await session.commit()
        return [s.id for s in suggestions]
