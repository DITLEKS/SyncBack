"""
Путь в репозитории: app/api/upload_utils.py

ИСПРАВЛЕНО: раньше upload_document/upload_file_source читали файл целиком в память
(`await file.read()`) до проверки лимита размера. Клиент без заголовка Content-Length
(chunked transfer encoding) мог обойти проверку в BodySizeLimitMiddleware (она смотрит
только на заголовок) и заставить backend буферизовать сколь угодно большой файл в
памяти процесса. Теперь чтение идёт чанками, а превышение лимита останавливает
чтение немедленно, не дожидаясь конца загрузки. Потоковая запись в MinIO (без
полной сборки в памяти перед записью) остаётся следующим шагом улучшения —
это требует изменения контракта FileStorage на приём стрима, вынесено как
отдельная задача.
"""
from fastapi import UploadFile

from app.domain.exceptions import FileTooLargeError

_CHUNK_SIZE = 1024 * 1024  # 1 МБ


async def read_upload_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    total = 0
    chunks: list[bytes] = []
    while chunk := await file.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise FileTooLargeError(f"Файл превышает лимит {max_bytes // (1024 * 1024)} МБ")
        chunks.append(chunk)
    return b"".join(chunks)
