"""P1: total не теряется, если страница правок пуста (offset за пределами выборки)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository


async def test_list_with_total_returns_real_total_for_empty_page() -> None:
    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.all.return_value = []
    session.execute.return_value = empty_result
    session.scalar.return_value = 82

    items, total = await SuggestionRepository(session).list_with_total(
        uuid.uuid4(), limit=50, offset=100
    )

    assert items == []
    assert total == 82
    session.scalar.assert_awaited_once()


async def test_list_with_total_returns_zero_when_nothing_matches() -> None:
    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.all.return_value = []
    session.execute.return_value = empty_result
    session.scalar.return_value = None

    items, total = await SuggestionRepository(session).list_with_total(
        uuid.uuid4(), limit=50, offset=0
    )

    assert items == []
    assert total == 0
