"""Ключи объектов в файловом хранилище.

Имя файла от пользователя в ключ не попадает: оно хранится в колонке name,
а объект называется по идентификатору записи. Расширение сохраняется,
потому что по нему выбирается парсер. Так в ключе не бывает путей,
управляющих символов и спецсимволов, которые ломают presigned-ссылки.
"""

from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath

_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,10}$")


def file_extension(filename: str) -> str:
    """Расширение в нижнем регистре вместе с точкой или пустая строка, если его нет."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix.lower()
    return suffix if _EXTENSION_RE.match(suffix) else ""


def document_storage_key(project_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    return f"projects/{project_id}/documents/{document_id}{file_extension(filename)}"


def source_storage_key(project_id: uuid.UUID, source_id: uuid.UUID, filename: str) -> str:
    return f"projects/{project_id}/sources/{source_id}{file_extension(filename)}"
