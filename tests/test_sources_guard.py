"""
P0-11: Тесты guard активного анализа в sources (P0-6).
"""
import pytest


@pytest.mark.anyio
async def test_create_source_locked_while_active_job(client):
    """POST /sources при активном job вернёт 423."""
    pytest.skip("Требуется fixtures с активным job")


@pytest.mark.anyio
async def test_delete_source_locked_while_active_job(client):
    """DELETE /sources/{id} при активном job вернёт 423."""
    pytest.skip("Требуется fixtures")


@pytest.mark.anyio
async def test_create_source_ok_without_active_job(client):
    """POST /sources без активного job вернёт 201."""
    pytest.skip("Требуется fixtures")
