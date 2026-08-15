"""
Порт для работы с файловым хранилищем. Единственная реализация на MVP — Minio
(app/infrastructure/storage/minio_storage.py), но домен и сервисы зависят только
от этого протокола, поэтому смена бэкенда хранения не потребует правок бизнес-логики.
"""

from typing import Protocol


class FileStorage(Protocol):
    async def upload(self, key: str, content: bytes, content_type: str) -> None: ...

    async def download(self, key: str) -> bytes: ...

    async def get_presigned_url(self, key: str, expires_in: int) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...
