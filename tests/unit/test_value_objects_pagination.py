"""KeysetPage: курсор либо задан целиком, либо отсутствует."""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.value_objects import KeysetPage


def test_first_page_has_no_cursor() -> None:
    assert KeysetPage(limit=10).has_cursor is False


def test_cursor_requires_both_fields() -> None:
    page = KeysetPage(limit=10, before_created_at=datetime.now(UTC), before_id=uuid.uuid4())
    assert page.has_cursor is True
    with pytest.raises(ValueError):
        KeysetPage(limit=10, before_created_at=datetime.now(UTC))
    with pytest.raises(ValueError):
        KeysetPage(limit=0)
