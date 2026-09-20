"""
P0-11: Тесты GET /editor (P0-8 aggregate).
"""
import pytest


@pytest.mark.anyio
async def test_editor_state_returns_document(client):
    """GET /editor возвращает document + suggestions + current_job."""
    pytest.skip("Требуется fixtures")


@pytest.mark.anyio
async def test_editor_state_404_for_unknown_document(client):
    """GET /editor для несуществующего document вернёт 404."""
    pytest.skip("Требуется fixtures")
