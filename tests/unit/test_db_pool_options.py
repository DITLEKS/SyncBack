"""Пул соединений API-движка настраивается из Settings и отключён для SQLite."""

from app.core.config import Settings
from app.infrastructure.db.session import _pool_options


def _settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        redis_url="redis://localhost:6379/0",
        minio_endpoint="localhost:9000",
        minio_access_key="a",
        minio_secret_key="b",
        minio_bucket="bucket",
        jwt_secret="x" * 32,
        cors_allowed_origins=["http://localhost:3000"],
        db_pool_size=3,
        db_max_overflow=4,
    )


def test_postgres_engine_uses_bounded_pool_with_pre_ping() -> None:
    options = _pool_options(_settings("postgresql+asyncpg://u:p@db/x"))
    assert options["pool_size"] == 3
    assert options["max_overflow"] == 4
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] > 0


def test_sqlite_engine_has_no_pool_options() -> None:
    assert _pool_options(_settings("sqlite+aiosqlite:///:memory:")) == {}
