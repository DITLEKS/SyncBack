"""Полный цикл работы S3FileStorage с реальным S3-шлюзом (в CI и compose — SeaweedFS).

Проверяем то, что нельзя проверить моком клиента: потоковую загрузку больше одной
части multipart, чтение, presigned URL с переопределённым Content-Disposition
и отсутствие доступа к объекту без подписи.
"""

import io
import uuid

import httpx

from app.domain.interfaces.file_storage import UploadContent
from app.infrastructure.storage.s3_storage import S3FileStorage

_PART_SIZE = 10 * 1024 * 1024


async def test_upload_download_presign_delete(s3_storage: S3FileStorage) -> None:
    key = f"tests/{uuid.uuid4()}.txt"
    payload = "строка с кириллицей\n".encode() * 2000

    await s3_storage.upload(key, UploadContent.from_bytes(payload, "text/plain"))
    try:
        assert await s3_storage.exists(key)
        assert await s3_storage.download(key) == payload

        url = await s3_storage.get_presigned_url(key, 60, download_name="Отчёт.txt")
        async with httpx.AsyncClient() as http:
            signed = await http.get(url)
            unsigned = await http.get(url.split("?", 1)[0])
        assert signed.status_code == 200
        assert signed.content == payload
        assert signed.headers["content-disposition"].startswith('attachment; filename="')
        assert (
            "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82.txt"
            in (signed.headers["content-disposition"])
        )
        assert unsigned.status_code == 403
    finally:
        await s3_storage.delete(key)

    assert not await s3_storage.exists(key)


async def test_multipart_upload_of_large_stream(s3_storage: S3FileStorage) -> None:
    key = f"tests/{uuid.uuid4()}.bin"
    size = _PART_SIZE + 1024 * 1024
    stream = io.BytesIO(b"x" * size)

    await s3_storage.upload(key, UploadContent(stream, size, "application/octet-stream"))
    try:
        assert len(await s3_storage.download(key)) == size
    finally:
        await s3_storage.delete(key)
