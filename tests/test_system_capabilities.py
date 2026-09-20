"""
P0-11: Тесты GET /api/v1/system/capabilities.
"""
import pytest


@pytest.mark.anyio
async def test_capabilities_returns_200(client):
    resp = await client.get("/api/v1/system/capabilities")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_capabilities_contains_required_fields(client):
    resp = await client.get("/api/v1/system/capabilities")
    data = resp.json()
    assert "supported_formats" in data
    assert "unsupported_formats" in data
    assert "max_file_size_mb" in data


@pytest.mark.anyio
async def test_capabilities_doc_is_unsupported(client):
    resp = await client.get("/api/v1/system/capabilities")
    data = resp.json()
    assert "doc" in data["unsupported_formats"]
    assert "doc" not in data["supported_formats"]


@pytest.mark.anyio
async def test_capabilities_no_auth_required(client):
    """capabilities должен работать без Authorization header."""
    resp = await client.get("/api/v1/system/capabilities")
    assert resp.status_code != 401
    assert resp.status_code != 403
