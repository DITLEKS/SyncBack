"""
Асинхронный движок и фабрика сессий SQLAlchemy.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

engine: Optional[AsyncEngine] = None
AsyncSessionLocal: Optional[async_sessionmaker[AsyncSession]] = None


def _init_engine() -> None:
    global engine, AsyncSessionLocal
    if AsyncSessionLocal is None:
        engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
            echo=settings.debug,
        )
        AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
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
async def db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager для использования вне FastAPI DI: тесты, скрипты,
    Celery-воркер. Использует ту же фабрику сессий (get_sessionmaker), что и
    get_db_session, поэтому поведение пула соединений (NullPool) остаётся консистентным.

    Пример:
        async with db_session_context() as session:
            ...
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session