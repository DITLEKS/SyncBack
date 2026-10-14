"""Просмотр и скачивание источников: presigned-ссылка для файла, адрес для url, текст файла."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.domain.exceptions import SourceNotFoundError, UnsupportedFormatError
from app.domain.policies import UploadLimits
from app.domain.services.source_service import SourceService
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

pytestmark = pytest.mark.asyncio


class FakeStorage:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.presigned: list[tuple[str, int, str | None]] = []

    async def upload(self, key, content):
        raise NotImplementedError

    async def download(self, key: str) -> bytes:
        return self.files[key]

    async def get_presigned_url(self, key: str, expires_in: int, download_name: str | None = None):
        self.presigned.append((key, expires_in, download_name))
        return f"https://s3.test/{key}?sig=1"

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
        download_url_ttl_seconds=600,
    )


def _file_source(name: str, key: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, storage_key=key, url=None)


def _url_source(url: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name="Док", storage_key=None, url=url)


async def test_file_source_gets_presigned_url_with_extension_in_name() -> None:
    storage = FakeStorage({})
    service = _service(storage)
    source = _file_source("Решение 1", "projects/p/sources/s.md")

    url, expires_in = await service.get_download_url(source)

    assert url.startswith("https://s3.test/projects/p/sources/s.md")
    assert expires_in == 600
    assert storage.presigned == [("projects/p/sources/s.md", 600, "Решение 1.md")]


async def test_download_name_keeps_existing_extension() -> None:
    storage = FakeStorage({})
    await _service(storage).get_download_url(_file_source("notes.MD", "projects/p/sources/s.md"))
    assert storage.presigned[0][2] == "notes.MD"


async def test_url_source_returns_its_address_without_ttl() -> None:
    storage = FakeStorage({})
    assert await _service(storage).get_download_url(_url_source("https://wiki.test/page")) == (
        "https://wiki.test/page",
        None,
    )
    assert storage.presigned == []


async def test_source_without_file_and_url_is_an_error() -> None:
    with pytest.raises(SourceNotFoundError):
        await _service(FakeStorage({})).get_download_url(_url_source(None))


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
