"""Ключи хранилища строятся из идентификаторов и безопасного расширения."""

import uuid

import pytest

from app.domain.storage_keys import document_storage_key, file_extension, source_storage_key

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OBJECT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.mark.parametrize(
    ("filename", "extension"),
    [
        ("spec.docx", ".docx"),
        ("Отчёт.DOCX", ".docx"),
        ("archive.tar.md", ".md"),
        ("../../etc/passwd", ""),
        ("C:\\Users\\me\\notes.txt", ".txt"),
        ("noext", ""),
        (".bashrc", ""),
        ("weird.ex t", ""),
        ("x." + "a" * 11, ""),
        ("trailing.", ""),
        ("", ""),
    ],
)
def test_file_extension(filename: str, extension: str) -> None:
    assert file_extension(filename) == extension


def test_keys_contain_only_ids_and_extension() -> None:
    assert (
        document_storage_key(PROJECT, OBJECT, "../../Отчёт (final).docx")
        == f"projects/{PROJECT}/documents/{OBJECT}.docx"
    )
    assert source_storage_key(PROJECT, OBJECT, "passwd") == f"projects/{PROJECT}/sources/{OBJECT}"
