"""Валидация настроек, от которых зависит безопасность."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@db/x",
    "REDIS_URL": "redis://redis:6379/0",
    "MINIO_ENDPOINT": "minio:9000",
    "MINIO_BUCKET": "bucket",
    "JWT_SECRET": "x" * 32,
    "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    optional = ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"]
    for key in list(_BASE) + list(extra) + optional + ["TRUSTED_PROXY_HOSTS"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_BASE, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_minio_service_credentials_with_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, MINIO_ACCESS_KEY="app", MINIO_SECRET_KEY="app-secret")
    assert (settings.minio_access_key, settings.minio_secret_key) == ("app", "app-secret")

    settings = _settings(monkeypatch, MINIO_ROOT_USER="root", MINIO_ROOT_PASSWORD="root-secret")
    assert (settings.minio_access_key, settings.minio_secret_key) == ("root", "root-secret")


def test_trusted_proxy_hosts_parsing_and_wildcard_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    creds = {"MINIO_ACCESS_KEY": "a", "MINIO_SECRET_KEY": "b"}
    assert _settings(monkeypatch, **creds).trusted_proxy_hosts == []
    assert _settings(
        monkeypatch, **creds, TRUSTED_PROXY_HOSTS="10.0.0.1, 10.0.0.2"
    ).trusted_proxy_hosts == [
        "10.0.0.1",
        "10.0.0.2",
    ]
    with pytest.raises(ValidationError):
        _settings(monkeypatch, **creds, TRUSTED_PROXY_HOSTS="*")


def test_rate_limit_storage_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, MINIO_ACCESS_KEY="a", MINIO_SECRET_KEY="b")
    assert settings.rate_limit_storage_uri == "memory://"
