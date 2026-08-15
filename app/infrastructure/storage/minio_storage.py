"""
Реализация FileStorage поверх Minio. Клиент minio-py синхронный, поэтому все вызовы
оборачиваются в asyncio.to_thread — иначе они блокировали бы event loop FastAPI.

Скачивание файлов идёт либо через presigned URL с ограниченным временем жизни,
либо потоково через backend (download) — прямых публичных ссылок на приватный бакет нет.
"""

import asyncio
import io
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings, get_settings


class MinioStorage:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._client = Minio(
            self._settings.minio_endpoint,
            access_key=self._settings.minio_root_user,
            secret_key=self._settings.minio_root_password,
            secure=self._settings.minio_secure,
        )
        self._bucket = self._settings.minio_bucket

    async def upload(self, key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            key,
            io.BytesIO(content),
            length=len(content),
            content_type=content_type,
        )

    async def download(self, key: str) -> bytes:
        def _download() -> bytes:
            response = self._client.get_object(self._bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_download)

    async def get_presigned_url(self, key: str, expires_in: int) -> str:
        return await asyncio.to_thread(
            self._client.presigned_get_object,
            self._bucket,
            key,
            expires=timedelta(seconds=expires_in),
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, key)

    async def exists(self, key: str) -> bool:
        def _exists() -> bool:
            try:
                self._client.stat_object(self._bucket, key)
                return True
            except S3Error:
                return False

        return await asyncio.to_thread(_exists)
