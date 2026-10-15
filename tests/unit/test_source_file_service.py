"""Скачивание источника: исходные байты, имя и проверка доступа до чтения файла."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.exceptions import SourceNotFoundError, UnsupportedFormatError
from app.domain.services.source_file_service import SourceFileService

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Решение", "Решение.docx"), ("Решение.DOCX", "Решение.DOCX")],
)
async def test_returns_original_bytes_and_filename(name, expected):
    project_id, source_id = uuid.uuid4(), uuid.uuid4()
    sources = SimpleNamespace(
        get_source=AsyncMock(return_value=SimpleNamespace(name=name, storage_key="sources/s.docx"))
    )
    storage = SimpleNamespace(download=AsyncMock(return_value=b"original bytes"))

    result = await SourceFileService(sources, storage).get_file(project_id, source_id)

    assert result.content == b"original bytes"
    assert result.filename == expected
    sources.get_source.assert_awaited_once_with(project_id, source_id)
    storage.download.assert_awaited_once_with("sources/s.docx")


async def test_missing_or_foreign_source_is_not_read_from_storage():
    sources = SimpleNamespace(get_source=AsyncMock(side_effect=SourceNotFoundError("Нет доступа")))
    storage = SimpleNamespace(download=AsyncMock())

    with pytest.raises(SourceNotFoundError):
        await SourceFileService(sources, storage).get_file(uuid.uuid4(), uuid.uuid4())

    storage.download.assert_not_awaited()


async def test_url_source_does_not_trigger_storage_or_external_request():
    sources = SimpleNamespace(get_source=AsyncMock(return_value=SimpleNamespace(storage_key=None)))
    storage = SimpleNamespace(download=AsyncMock())

    with pytest.raises(UnsupportedFormatError):
        await SourceFileService(sources, storage).get_file(uuid.uuid4(), uuid.uuid4())

    storage.download.assert_not_awaited()
