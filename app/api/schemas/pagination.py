"""
Общая схема постраничного ответа для list-эндпоинтов.
"""

from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
