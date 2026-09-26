"""
Общие схемы постраничных ответов для list-эндпоинтов.

Page[T]       — классическая OFFSET-пагинация (limit/offset/total).
CursorPage[T] — cursor-based (keyset) пагинация (after_id → next_cursor).
                Не содержит total — keyset COUNT(*) дорог и не нужен клиенту
                при бесконечной прокрутке. Клиент знает, что достиг конца,
                когда has_more=False.
"""

from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class CursorPage(BaseModel, Generic[T]):
    """Cursor-based (keyset) постраничный ответ.

    next_cursor — UUID последнего элемента страницы; передаётся как ?after=<uuid>
                  в следующем запросе. None если страница пустая или последняя.
    has_more    — True если за этой страницей есть ещё элементы.
    """

    items: list[T]
    next_cursor: UUID | None
    has_more: bool
