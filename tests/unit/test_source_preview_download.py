"""Просмотр источников: текст файла через реестр парсеров, у url-источника текста нет."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.domain.exceptions import UnsupportedFormatError
from app.domain.policies import UploadLimits
from app.domain.services.source_service import SourceService
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

pytestmark = pytest.mark.asyncio


class FakeStorage:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def upload(self, key, content):
        raise NotImplementedError

    async def download(self, key: str) -> bytes:
        return self.files[key]

    async def delete(self, key: str) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return key in self.files


def _service(storage: FakeStorage) -> SourceService:
    return SourceService(
        uow=None,  # type: ignore[arg-type]
        file_storage=storage,
        upload_limits=UploadLimits.from_megabytes(1),
        parser_registry=DocumentParserRegistry(),
    )


def _file_source(name: str, key: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, storage_key=key, url=None)


def _url_source(url: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name="Док", storage_key=None, url=url)


async def test_file_source_text_is_parsed_by_storage_key_extension() -> None:
    storage = FakeStorage({"projects/p/sources/s.md": "# Заголовок\n\nАбзац.".encode()})
    source = _file_source("Заметка", "projects/p/sources/s.md")
    text = await _service(storage).get_source_text(source)
    assert "Абзац." in text


async def test_url_source_has_no_text() -> None:
    with pytest.raises(UnsupportedFormatError):
        await _service(FakeStorage({})).get_source_text(_url_source("https://wiki.test"))


async def test_blocked_format_is_reported_as_unsupported() -> None:
    storage = FakeStorage({"projects/p/sources/s.doc": b"\xd0\xcf"})
    with pytest.raises(UnsupportedFormatError):
        await _service(storage).get_source_text(_file_source("Старый", "projects/p/sources/s.doc"))
