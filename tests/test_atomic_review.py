"""
P0-11: Тесты PUT /editor/{document_id}/review (P0-2).

TODO: заполнить fixtures create_project / create_document / auth_headers.
"""
import pytest


@pytest.mark.anyio
async def test_atomic_review_wrong_version_returns_409(client):
    """Если review_version не совпадает — 409 Conflict.
    
    TODO: заменить placeholder-id на реальные UUID через fixtures.
    """
    pytest.skip("Требуется fixtures с реальным документом")


@pytest.mark.anyio
async def test_atomic_review_overlap_ids_returns_422(client):
    """Один UUID в обоих списках — 422.
    
    TODO: заменить placeholder-id.
    """
    pytest.skip("Требуется fixtures")


@pytest.mark.anyio
async def test_atomic_review_correct_version_returns_200(client):
    """Правильная версия — 200 и новая review_version."""
    pytest.skip("Требуется fixtures")
