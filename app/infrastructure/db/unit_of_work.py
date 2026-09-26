"""
SQLAlchemy-реализация Unit of Work.

Единственное место в приложении, где вызываются session.commit() и session.rollback().
Все репозитории получают ту же самую сессию — все изменения фиксируются атомарно.

Правило:
  - Никогда не вызывайте session.commit() внутри репозиториев.
  - Никогда не вызывайте uow.commit() более одного раза за операцию
    (исключение — Celery-задачи с промежуточными чекпойнтами: каждый чекпойнт
    создаёт новый `async with uow` блок).
  - H-4: refresh() доступен через IUnitOfWork.refresh() — не обращайся к uow._session напрямую.
  - HIGH-A: self.users добавлен, чтобы избежать AttributeError при uow.users.
"""
from __future__ import annotations

from typing import Any
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.audit_log_repository import AuditLogRepository
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository
from app.infrastructure.db.repositories.user_repository import UserRepository


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """Конкретный UoW поверх AsyncSession.

    Создаётся per-request через FastAPI Depends (см. core/dependencies.py).
    Каждая HTTP-операция — одна транзакция. Celery-задачи создают свой экземпляр
    через isolated_uow() (см. infrastructure/db/session.py).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

        # Core repositories
        self.documents   = DocumentRepository(session)
        self.suggestions = SuggestionRepository(session)
        self.jobs        = AnalysisJobRepository(session)
        self.audit       = AuditLogRepository(session)

        # Extended repositories
        self.projects    = ProjectRepository(session)
        self.sources     = SourceRepository(session)
        self.dashboard   = DashboardRepository(session)

        # Auth repositories (HIGH-A: добавлен, чтобы uow.users не давал AttributeError)
        self.users       = UserRepository(session)

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        # Сессия закрывается владельцем (FastAPI DI / isolated_uow).

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def refresh(self, obj: Any, attribute_names: list[str] | None = None) -> None:
        """H-4: обновить ORM-объект из БД, не обращаясь к _session напрямую."""
        await self._session.refresh(obj, attribute_names=attribute_names)
