"""
Общая схема постраничного ответа для list-эндпоинтов.

ИСПРАВЛЕНО: раньше GET /projects, /documents, /sources, /suggestions возвращали весь
результат целиком без LIMIT/OFFSET. По CustDev-данным клиенты держат 300+
документов на продукт — без потолка объём ответа и память на сериализацию растут
линейно без ограничения при росте данных. Фронтенда ещё нет — это безопасный момент
изменить форму ответа этих endpoint'ов с "плоского списка" на объект с метаданными.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
