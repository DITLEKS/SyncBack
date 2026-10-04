"""
Конфигурация Alembic для асинхронного SQLAlchemy-движка.

Каждая миграция коммитится отдельно (transaction_per_migration): PostgreSQL
не позволяет использовать новое значение enum в той же транзакции, где оно
добавлено, а несколько ревизий делают ADD VALUE и тут же используют значение.

Alembic нужен только адрес БД, поэтому читаем DATABASE_URL напрямую, не собирая
полный Settings: в окружении миграций (CI, job migrate) нет и не должно быть
JWT-секрета, адреса хранилища и прочих настроек приложения.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import *  # noqa: F401,F403

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Локальный запуск без экспорта переменной: берём значение из .env, как делает приложение.
    from app.core.config import get_settings  # noqa: PLC0415

    return get_settings().database_url


config.set_main_option("sqlalchemy.url", _database_url())


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
