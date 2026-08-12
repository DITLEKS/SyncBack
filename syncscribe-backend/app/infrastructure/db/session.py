"""
Асинхронный движок и фабрика сессий SQLAlchemy.
"""

from collections.abc import AsyncGenerator
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
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session
