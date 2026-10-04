"""
Реализация FileStorage поверх S3-совместимого хранилища (в compose — SeaweedFS).

Клиент minio-py используется как универсальный S3-клиент: он синхронный, поэтому все
вызовы оборачиваются в asyncio.to_thread — иначе они блокировали бы event loop FastAPI.

Скачивание файлов идёт либо через presigned URL с ограниченным временем жизни,
либо потоково через backend (download) — прямых публичных ссылок на приватный бакет нет.
"""

import asyncio
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings, get_settings
from app.core.content_disposition import content_disposition
from app.domain.interfaces.file_storage import UploadContent

_PART_SIZE_BYTES = 10 * 1024 * 1024


class S3FileStorage:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._client = Minio(
            self._settings.s3_endpoint,
            access_key=self._settings.s3_access_key,
            secret_key=self._settings.s3_secret_key,
            secure=self._settings.s3_secure,
        )
        self._bucket = self._settings.s3_bucket

    async def upload(self, key: str, content: UploadContent) -> None:
        # minio-py читает поток частями по part_size и при больших файлах сам
        # переходит на multipart upload, поэтому файл целиком в памяти не нужен.
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            key,
            content.stream,
            length=content.size,
            content_type=content.content_type,
            part_size=_PART_SIZE_BYTES,
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

    async def get_presigned_url(
        self, key: str, expires_in: int, download_name: str | None = None
    ) -> str:
        response_headers: dict[str, str | list[str] | tuple[str]] | None = (
            {"response-content-disposition": content_disposition(download_name)}
            if download_name
            else None
        )
        return await asyncio.to_thread(
            self._client.presigned_get_object,
            self._bucket,
            key,
            expires=timedelta(seconds=expires_in),
            response_headers=response_headers,
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
