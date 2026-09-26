"""
Абстрактный Unit of Work — единственная точка фиксации транзакции.

Правила:
  - Репозитории только добавляют/изменяют объекты в сессии.
  - Вся логика commit()/rollback() — здесь.
  - domain/services зависят ТОЛЬКО от этого интерфейса;
    инфраструктурная реализация (SqlAlchemyUnitOfWork) подключается через DI.
  - __aenter__ и __aexit__ объявлены @abstractmethod, чтобы тестовые фейки
    были обязаны реализовать их явно.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.interfaces.repositories import (
        IAnalysisJobRepository,
        IAuditLogRepository,
        IDocumentRepository,
        ISuggestionRepository,
    )


class IUnitOfWork(ABC):
    """Async context-manager, управляющий временем жизни транзакции.

    Использование:
        async with uow:
            doc = await uow.documents.get_by_id(doc_id)
            doc.status = DocumentStatus.READY
            suggestion_ids = await uow.suggestions.list_pending_ids(job_id)
            await uow.suggestions.bulk_accept(suggestion_ids, user_id)
            await uow.commit()   # один flush на всю операцию
    """

    documents: "IDocumentRepository"
    suggestions: "ISuggestionRepository"
    jobs: "IAnalysisJobRepository"
    audit: "IAuditLogRepository"

    @abstractmethod
    async def __aenter__(self) -> "IUnitOfWork":
        """Войти в транзакционный контекст."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """При исключении — rollback; иначе — ничего (commit явный)."""

    @abstractmethod
    async def commit(self) -> None:
        """Зафиксировать все изменения текущей транзакции."""

    @abstractmethod
    async def rollback(self) -> None:
        """Откатить незафиксированные изменения."""
