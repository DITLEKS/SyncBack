"""
Проверяем, что presigned URL на скачивание документа действительно запрашивается с
ограниченным сроком жизни (timedelta из settings.minio_presigned_url_expire_seconds),
а не выдаётся бессрочным. Реального Minio для теста не поднимаем — подменяем внутренний
клиент minio.Minio заглушкой и проверяем, с каким аргументом expires он был вызван.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.infrastructure.storage.minio_storage import MinioStorage


def _fake_settings(expire_seconds: int = 300):
    return SimpleNamespace(
        minio_endpoint="minio:9000",
        minio_root_user="user",
        minio_root_password="password",
        minio_bucket="bucket",
        minio_secure=False,
        minio_presigned_url_expire_seconds=expire_seconds,
    )


@pytest.mark.parametrize("expire_seconds", [60, 300, 3600])
async def test_presigned_url_uses_configured_ttl(monkeypatch, expire_seconds):
    storage = MinioStorage(_fake_settings(expire_seconds))
    fake_client = MagicMock()
    fake_client.presigned_get_object.return_value = "https://example.invalid/signed"
    storage._client = fake_client  # подменяем внутренний minio-клиент на мок

    url = await storage.get_presigned_url("some/key", expire_seconds)

    assert url == "https://example.invalid/signed"
    fake_client.presigned_get_object.assert_called_once()
    _, kwargs = fake_client.presigned_get_object.call_args
    assert kwargs["expires"] == timedelta(seconds=expire_seconds)
    # Явно фиксируем инвариант: TTL никогда не может быть "бессрочным" (None/0)
    assert kwargs["expires"] > timedelta(seconds=0)
