"""Приём загруженного файла без чтения его в память."""

import os

from fastapi import UploadFile

from app.domain.exceptions import FileTooLargeError
from app.domain.interfaces.file_storage import UploadContent


def upload_content(file: UploadFile, max_bytes: int) -> UploadContent:
    """Обернуть загруженный файл в UploadContent, проверив размер.

    Starlette уже сохранил часть multipart во временный файл (в памяти до 1 МБ,
    дальше на диске), а общий объём тела ограничен BodySizeLimitMiddleware.
    Здесь остаётся узнать точный размер файла и вернуть поток в начало.
    """
    stream = file.file
    size = file.size
    if size is None:
        size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size > max_bytes:
        raise FileTooLargeError(f"Файл превышает лимит {max_bytes // (1024 * 1024)} МБ")
    return UploadContent(
        stream=stream,
        size=size,
        content_type=file.content_type or "application/octet-stream",
    )
