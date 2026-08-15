"""
Асинхронный движок и фабрика сессий SQLAlchemy.

Путь в репозитории: app/infrastructure/db/session.py
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def _init_engine() -> None:
    """Лениво создаёт движок и sessionmaker при первом обращении.

    ИСПРАВЛЕНО: раньше `settings = get_settings()` вызывался на уровне модуля, т.е.
    простой импорт этого файла (например, транзитивно через app.main -> ... ->
    app.core.dependencies -> app.infrastructure.db.session) требовал наличия всех обязательных полей
    конфигурации (DATABASE_URL, REDIS_URL, MINIO_*, JWT_SECRET) без какого-либо реального
    обращения к БД. В CI, где нет .env и явных переменных окружения, это
    приводило к pydantic.ValidationError уже на этапе сбора тестов — падал даже безобидный
    health-check тест, которому БД не нужна. Теперь настройки читаются только в момент, когда
    движок действительно нужен.
    """
    global engine, AsyncSessionLocal
    if AsyncSessionLocal is None:
        settings = get_settings()
        engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
            echo=settings.debug,
        )
        AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Единый sessionmaker для FastAPI request-scoped использования.

    FastAPI/Uvicorn гарантированно работает на одном и том же event loop весь
    жизненный цикл процесса, поэтому кешировать движок здесь безопасно.

    ВАЖНО — НЕ использовать эту фабрику:
    - из Celery-задач (каждый вызов таска может исполняться в новом event loop,
      т.к. `asyncio.run()` создаёт новый loop на каждый вызов);
    - из тестов на pytest-asyncio (по умолчанию — новый event loop на каждый тест);
    - из любого другого кода, для которого нет гарантии одного и того же event loop
      на всё время жизни процесса.

    Движок, созданный на одном event loop, нельзя использовать на другом —
    это приводит к RuntimeError ("... attached to a different loop"). Для таких
    случаев используйте isolated_db_session().
    """
    if AsyncSessionLocal is None:
        _init_engine()
    return AsyncSessionLocal  # type: ignore[return-value]


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Generator-зависимость для FastAPI Depends(). Не использовать напрямую вне DI —
    FastAPI сам разворачивает генератор через __anext__, а не async with."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


@asynccontextmanager
async def isolated_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager, создающий отдельный AsyncEngine + сессию строго на время
    одного вызова и гарантированно закрывающий движок при выходе.

    Используется везде, где нет гарантии одного и того же event loop на всё время
    жизни процесса: Celery-задачи, integration-тесты на pytest-asyncio (по умолчанию —
    новый event loop на каждый тест) и одноразовые скрипты. Движок никогда не покидает
    event loop, в котором был создан, поэтому ошибка "attached to a different loop"
    структурно невозможна: у каждого вызова свой изолированный движок.

    Пример:
        async with isolated_db_session() as session:
            ...
    """
    local_settings = get_settings()
    local_engine = create_async_engine(
        local_settings.database_url, poolclass=NullPool, echo=local_settings.debug
    )
    local_sessionmaker = async_sessionmaker(local_engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with local_sessionmaker() as session:
            yield session
    finally:
        await local_engine.dispose()


@asynccontextmanager
async def db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Тонкая обёртка над isolated_db_session() для использования вне FastAPI DI:
    тесты, скрипты, Celery-воркер.

    ИСПРАВЛЕНО: раньше эта функция ошибочно переиспользовала общий request-scoped
    sessionmaker (get_sessionmaker()), что при вызове из Celery-задач или
    pytest-asyncio тестов с per-test event loop приводило к RuntimeError
    "attached to a different loop". Теперь она всегда создаёт изолированный движок
    и безопасна для использования на любом event loop.

    Пример:
        async with db_session_context() as session:
            ...
    """
    async with isolated_db_session() as session:
        yield session
