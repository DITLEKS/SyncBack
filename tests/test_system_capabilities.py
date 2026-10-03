"""GET /api/v1/system/capabilities: публичный контракт для фронтенда."""

import pytest


@pytest.mark.anyio
async def test_capabilities_returns_200_without_auth(client):
    resp = await client.get("/api/v1/system/capabilities")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_capabilities_contains_required_sections(client):
    data = (await client.get("/api/v1/system/capabilities")).json()
    assert {"upload", "analysis", "review", "export"} <= data.keys()
    assert {"max_size_mb", "max_size_bytes", "supported_formats", "supported_mime_types"} <= data[
        "upload"
    ].keys()


@pytest.mark.anyio
async def test_capabilities_doc_is_unsupported(client):
    """Старый .doc парсер не читает, фронт не должен предлагать его к загрузке."""
    data = (await client.get("/api/v1/system/capabilities")).json()
    assert "doc" not in data["upload"]["supported_formats"]
    assert "docx" in data["upload"]["supported_formats"]
