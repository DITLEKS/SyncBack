"""Загрузка файлов: точный размер, содержимое без искажений и 413 сверх лимита."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import app
from tests.contract.conftest import InMemoryFileStorage, create_project, register_and_login

pytestmark = pytest.mark.asyncio

_MIB = 1024 * 1024


@pytest.fixture
def one_megabyte_limit() -> Iterator[None]:
    settings = get_settings().model_copy(update={"max_upload_size_mb": 1})
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


async def test_large_document_is_stored_intact(
    client: AsyncClient, file_storage: InMemoryFileStorage
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    # Больше порога SpooledTemporaryFile в Starlette (1 МБ): файл уходит на диск.
    payload = bytes(range(256)) * (3 * _MIB // 256)

    response = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("big.txt", payload, "text/plain")},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["size_bytes"] == len(payload)
    assert list(file_storage.files.values()) == [payload]


@pytest.mark.usefixtures("one_megabyte_limit")
async def test_document_and_file_source_over_limit_are_rejected(
    client: AsyncClient, file_storage: InMemoryFileStorage
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)
    too_big = b"x" * (_MIB + 1)

    document = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("big.txt", too_big, "text/plain")},
        headers=headers,
    )
    source = await client.post(
        f"/api/v1/projects/{project_id}/sources/file",
        data={"name": "big"},
        files={"file": ("big.txt", too_big, "text/plain")},
        headers=headers,
    )
    global_document = await client.post(
        "/api/v1/documents",
        data={"project_id": project_id},
        files={"file": ("big.txt", too_big, "text/plain")},
        headers=headers,
    )

    assert document.status_code == 413, document.text
    assert source.status_code == 413, source.text
    assert global_document.status_code == 413, global_document.text
    assert file_storage.files == {}


@pytest.mark.usefixtures("one_megabyte_limit")
async def test_file_exactly_at_limit_is_accepted(
    client: AsyncClient, file_storage: InMemoryFileStorage
) -> None:
    headers = await register_and_login(client)
    project_id = await create_project(client, headers)

    response = await client.post(
        f"/api/v1/projects/{project_id}/sources/file",
        data={"name": "edge"},
        files={"file": ("edge.txt", b"y" * _MIB, "text/plain")},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    assert [len(v) for v in file_storage.files.values()] == [_MIB]
