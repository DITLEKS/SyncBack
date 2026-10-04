"""Порт файлового хранилища. Домен и сервисы зависят только от него, реализация — S3-совместимое хранилище."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class UploadContent:
    """Содержимое для записи: поток, его точный размер и MIME-тип.

    Поток читается хранилищем частями, поэтому крупный файл не собирается в памяти.
    """

    stream: BinaryIO
    size: int
    content_type: str

    @classmethod
    def from_bytes(cls, data: bytes, content_type: str) -> UploadContent:
        return cls(io.BytesIO(data), len(data), content_type)


class FileStorage(Protocol):
    async def upload(self, key: str, content: UploadContent) -> None: ...

    async def download(self, key: str) -> bytes: ...

    async def get_presigned_url(
        self, key: str, expires_in: int, download_name: str | None = None
    ) -> str:
        """Временная ссылка на скачивание; download_name — имя файла для браузера."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...
