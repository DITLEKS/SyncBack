from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import app.infrastructure.cache.redis_client as redis_client_module
from app.infrastructure.db.session import db_session_context
from app.infrastructure.storage.minio_storage import MinioStorage


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Всё в tests/integration требует реальных Postgres/Redis/MinIO.

    Маркер ставится централизованно, чтобы забытый pytestmark в отдельном файле
    не затягивал эти тесты в unit-прогон (там они зависают на подключении к БД).
    """
    integration_dir = Path(__file__).parent
    for item in items:
        if integration_dir in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with db_session_context() as session:
        yield session


# Alias used by test_e2e_document_lifecycle.py — both names resolve to the
# same real PostgreSQL session so all E2E helpers can share one transaction.
@pytest_asyncio.fixture
async def pg_session(db_session: AsyncSession) -> AsyncSession:
    yield db_session


@pytest.fixture
def minio_storage() -> MinioStorage:
    return MinioStorage()


@pytest_asyncio.fixture(autouse=True)
async def _isolated_redis_client():
    """
    ИСПРАВЛЕНО: app.infrastructure.cache.redis_client.get_redis_client() — процесс-wide
    синглтон (модульная переменная _redis_client), который переживает event loop, на
    котором был создан. pytest-asyncio по умолчанию создаёт новый event loop на каждый
    тест (function-scoped), поэтому асинхронное Redis-соединение, установленное в одном
    тесте, оказывается привязанным к уже закрытому event loop в следующем — при первом
    же обращении к Redis (например, через LoginRateLimiter при login) это падает с
    `RuntimeError: Event loop is closed` / `AttributeError: 'NoneType' object has no
    attribute 'send'` внутри ProactorEventLoop на Windows.

    Эта autouse-фикстура сбрасывает глобальный синглтон перед и после каждого теста,
    гарантируя, что клиент создаётся заново на актуальном event loop. В проде (FastAPI
    с одним постоянным event loop на весь жизненный цикл процесса) проблема не
    проявляется — фикс нужен только для тестового окружения.
    """
    redis_client_module._redis_client = None
    yield
    client = redis_client_module._redis_client
    if client is not None:
        await client.aclose()
    redis_client_module._redis_client = None
