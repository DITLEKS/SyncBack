"""Валидация настроек, от которых зависит безопасность."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@db/x",
    "REDIS_URL": "redis://redis:6379/0",
    "S3_ENDPOINT": "seaweedfs:8333",
    "S3_BUCKET": "bucket",
    "JWT_SECRET": "x" * 32,
    "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    optional = [
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "MINIO_ENDPOINT",
        "MINIO_BUCKET",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ]
    for key in list(_BASE) + list(extra) + optional + ["TRUSTED_PROXY_HOSTS"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_BASE, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_s3_credentials_with_legacy_minio_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, S3_ACCESS_KEY="app", S3_SECRET_KEY="app-secret")
    assert (settings.s3_access_key, settings.s3_secret_key) == ("app", "app-secret")

    settings = _settings(monkeypatch, MINIO_ACCESS_KEY="app", MINIO_SECRET_KEY="app-secret")
    assert (settings.s3_access_key, settings.s3_secret_key) == ("app", "app-secret")

    settings = _settings(monkeypatch, MINIO_ROOT_USER="root", MINIO_ROOT_PASSWORD="root-secret")
    assert (settings.s3_access_key, settings.s3_secret_key) == ("root", "root-secret")


def test_s3_endpoint_and_bucket_accept_legacy_minio_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("S3_ENDPOINT", "S3_BUCKET"):
        monkeypatch.delenv(key, raising=False)
    env = {k: v for k, v in _BASE.items() if k not in ("S3_ENDPOINT", "S3_BUCKET")}
    env.update(
        MINIO_ENDPOINT="minio:9000",
        MINIO_BUCKET="legacy-bucket",
        S3_ACCESS_KEY="a",
        S3_SECRET_KEY="b",
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert (settings.s3_endpoint, settings.s3_bucket) == ("minio:9000", "legacy-bucket")


def test_trusted_proxy_hosts_parsing_and_wildcard_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    creds = {"S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "b"}
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
    settings = _settings(monkeypatch, S3_ACCESS_KEY="a", S3_SECRET_KEY="b")
    assert settings.rate_limit_storage_uri == "memory://"
