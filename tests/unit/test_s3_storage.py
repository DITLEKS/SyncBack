"""
Проверяем, что presigned URL на скачивание документа действительно запрашивается с
ограниченным сроком жизни (timedelta из settings.s3_presigned_url_expire_seconds),
а не выдаётся бессрочным. Реального хранилища для теста не поднимаем — подменяем
внутренний S3-клиент заглушкой и проверяем, с каким аргументом expires он был вызван.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.interfaces.file_storage import UploadContent
from app.infrastructure.storage.s3_storage import S3FileStorage


def _fake_settings(expire_seconds: int = 300):
    return SimpleNamespace(
        s3_endpoint="seaweedfs:8333",
        s3_access_key="user",
        s3_secret_key="password",
        s3_bucket="bucket",
        s3_secure=False,
        s3_presigned_url_expire_seconds=expire_seconds,
    )


@pytest.mark.parametrize("expire_seconds", [60, 300, 3600])
async def test_presigned_url_uses_configured_ttl(monkeypatch, expire_seconds):
    storage = S3FileStorage(_fake_settings(expire_seconds))
    fake_client = MagicMock()
    fake_client.presigned_get_object.return_value = "https://example.invalid/signed"
    storage._client = fake_client  # подменяем внутренний S3-клиент на мок

    url = await storage.get_presigned_url("some/key", expire_seconds)

    assert url == "https://example.invalid/signed"
    fake_client.presigned_get_object.assert_called_once()
    _, kwargs = fake_client.presigned_get_object.call_args
    assert kwargs["expires"] == timedelta(seconds=expire_seconds)
    # Явно фиксируем инвариант: TTL никогда не может быть "бессрочным" (None/0)
    assert kwargs["expires"] > timedelta(seconds=0)


async def test_presigned_url_sets_download_name():
    storage = S3FileStorage(_fake_settings())
    fake_client = MagicMock()
    fake_client.presigned_get_object.return_value = "https://example.invalid/signed"
    storage._client = fake_client

    await storage.get_presigned_url("some/key", 60, download_name='Отчёт "v2".docx')

    _, kwargs = fake_client.presigned_get_object.call_args
    disposition = kwargs["response_headers"]["response-content-disposition"]
    assert disposition.startswith('attachment; filename="')
    assert '"v2"' not in disposition.split(";")[1]
    assert "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82" in disposition


async def test_presigned_url_without_name_has_no_response_headers():
    storage = S3FileStorage(_fake_settings())
    fake_client = MagicMock()
    fake_client.presigned_get_object.return_value = "https://example.invalid/signed"
    storage._client = fake_client

    await storage.get_presigned_url("some/key", 60)

    _, kwargs = fake_client.presigned_get_object.call_args
    assert kwargs["response_headers"] is None


async def test_upload_passes_stream_with_exact_length():
    storage = S3FileStorage(_fake_settings())
    fake_client = MagicMock()
    storage._client = fake_client
    content = UploadContent.from_bytes(b"payload", "text/plain")

    await storage.upload("some/key", content)

    args, kwargs = fake_client.put_object.call_args
    assert args == ("bucket", "some/key", content.stream)
    assert kwargs["length"] == 7
    assert kwargs["content_type"] == "text/plain"
    assert kwargs["part_size"] >= 5 * 1024 * 1024  # минимальная часть multipart в S3
