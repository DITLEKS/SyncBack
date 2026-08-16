"""
Конфигурация Alembic для асинхронного SQLAlchemy-движка.

ИСПРАВЛЕНО: по умолчанию Alembic выполняет все накопившиеся миграции в одной команде
`upgrade head` в ОДНОЙ транзакции. Миграция 0002 делает
`ALTER TYPE audit_action ADD VALUE 'download'`, а миграция 0003 сразу же использует
это значение в CHECK CONSTRAINT. PostgreSQL требует, чтобы новое значение enum было
закоммичено, прежде чем его можно использовать — без этого Postgres падает с
`UnsafeNewEnumValueUsageError`, что делало `alembic upgrade head` на любой чистой БД
полностью нерабочим. Теперь `transaction_per_migration=True` заставляет каждую
миграцию коммититься отдельно, прежде чем начнётся следующая.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import *  # noqa: F401,F403

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
