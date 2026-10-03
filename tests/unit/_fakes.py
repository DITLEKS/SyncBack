"""Минимальный Unit of Work для unit-тестов domain-сервисов.

Репозитории передаются готовыми заглушками (AsyncMock / SimpleNamespace);
UoW только связывает их и фиксирует факт commit/rollback.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock


class FakeUnitOfWork:
    def __init__(self, **repositories: Any) -> None:
        for name, repository in repositories.items():
            setattr(self, name, repository)
        self.session = AsyncMock()
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, obj: Any, attribute_names: list[str] | None = None) -> None:
        return None
