"""
Абстрактный Unit of Work — единственная точка фиксации транзакции.

Правила:
  - Репозитории только добавляют/изменяют объекты в сессии.
  - Вся логика commit()/rollback() — здесь.
  - domain/services зависят ТОЛЬКО от этого интерфейса;
    инфраструктурная реализация (SqlAlchemyUnitOfWork) подключается через DI.
  - __aenter__ и __aexit__ объявлены @abstractmethod, чтобы тестовые фейки
    были обязаны реализовать их явно.

Namespace-атрибуты (все объявлены здесь для type-checker'а):
  documents   — IDocumentRepository
  suggestions — ISuggestionRepository
  jobs        — IAnalysisJobRepository
  audit       — IAuditLogRepository
  projects    — IProjectRepository
  sources     — ISourceRepository
  dashboard   — IDashboardRepository
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domain.interfaces.repositories import (
        IAnalysisJobRepository,
        IAuditLogRepository,
        IDashboardRepository,
        IDocumentRepository,
        IProjectRepository,
        ISourceRepository,
        ISuggestionRepository,
    )


class IUnitOfWork(ABC):
    """Асинхронный context-manager, управляющий временем жизни транзакции.

    Использование:
        async with uow:
            doc = await uow.documents.get_by_id(doc_id)
            await uow.suggestions.bulk_update_status(decisions)
            await uow.commit()   # один flush на всю операцию
    """

    # Core
    documents:   "IDocumentRepository"
    suggestions: "ISuggestionRepository"
    jobs:        "IAnalysisJobRepository"
    audit:       "IAuditLogRepository"

    # Extended (добавлены при переводе на UoW)
    projects:    "IProjectRepository"
    sources:     "ISourceRepository"
    dashboard:   "IDashboardRepository"

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

    @abstractmethod
    async def refresh(self, obj: Any, attribute_names: list[str] | None = None) -> None:
        """Обновить ORM-объект из БД через интерфейс (H-4).

        Используется в воркере вместо прямого обращения к uow._session.
        attribute_names: список ленивых атрибутов для загрузки (напр., ["sources"]).
        """
