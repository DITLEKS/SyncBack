"""upload_content: размер без чтения файла в память и поток с начала."""

import io

import pytest
from fastapi import UploadFile

from app.api.upload_utils import upload_content
from app.domain.exceptions import FileTooLargeError
from app.domain.interfaces.file_storage import UploadContent


def test_size_is_measured_by_seek_when_unknown() -> None:
    stream = io.BytesIO(b"abcdef")
    stream.seek(3)
    content = upload_content(UploadFile(stream, filename="a.txt"), max_bytes=10)
    assert content.size == 6
    assert content.content_type == "application/octet-stream"
    assert content.stream.read() == b"abcdef"


def test_over_limit_raises() -> None:
    with pytest.raises(FileTooLargeError):
        upload_content(UploadFile(io.BytesIO(b"x" * 11), filename="a.txt", size=11), max_bytes=10)


def test_from_bytes_wraps_data() -> None:
    content = UploadContent.from_bytes(b"hello", "text/plain")
    assert (content.size, content.content_type, content.stream.read()) == (
        5,
        "text/plain",
        b"hello",
    )
