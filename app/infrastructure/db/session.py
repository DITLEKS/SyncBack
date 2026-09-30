"""
Асинхронный движок и фабрика сессий SQLAlchemy.

Путь в репозитории: app/infrastructure/db/session.py
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def _init_engine() -> None:
    """Лениво создаёт движок и sessionmaker при первом обращении.

    Настройки читаются здесь, а не на уровне модуля, чтобы простой импорт
    session.py не требовал наличия переменных окружения (DATABASE_URL и т.д.).
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

    WARNING: НЕ использовать из Celery-задач, pytest-asyncio тестов или любого
    кода, где нет гарантии одного event loop на весь жизненный цикл процесса.
    Движок, созданный на одном event loop, нельзя переиспользовать на другом
    (RuntimeError: attached to a different loop).
    Для Celery и тестов используйте isolated_db_session() или isolated_uow().
    """
    if AsyncSessionLocal is None:
        _init_engine()
    return AsyncSessionLocal  # type: ignore[return-value]


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Generator-зависимость для FastAPI Depends().

    Не использовать напрямую вне FastAPI DI — FastAPI сам разворачивает
    генератор через __anext__, а не async with.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


@asynccontextmanager
async def isolated_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Создаёт отдельный AsyncEngine и сессию строго на время одного вызова.

    Использовать везде, где нет гарантии одного event loop:
    Celery-задачи, pytest-asyncio тесты (новый loop на каждый тест),
    одноразовые скрипты. Движок утилизируется при выходе из контекста —
    ошибка «attached to a different loop» структурно невозможна.

    Пример:
        async with isolated_db_session() as session:
            result = await session.execute(select(User))
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
async def isolated_uow() -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
    """UoW-аналог isolated_db_session() для Celery, тестов и скриптов.

    Создаёт изолированный движок и возвращает полностью собранный
    SqlAlchemyUnitOfWork. Гарантирует те же свойства event-loop-изоляции.

    Пример:
        async with isolated_uow() as uow:
            doc = await uow.documents.get_by_id(doc_id)
            await uow.commit()
    """
    # Импорт здесь, а не на module level, чтобы избежать
    # циклического импорта (unit_of_work -> repositories -> session).
    from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: PLC0415

    async with isolated_db_session() as session:
        yield SqlAlchemyUnitOfWork(session)


@asynccontextmanager
async def db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Обёртка над isolated_db_session() для кода вне FastAPI DI
    (Celery, скрипты, тесты).
    """
    async with isolated_db_session() as session:
        yield session
