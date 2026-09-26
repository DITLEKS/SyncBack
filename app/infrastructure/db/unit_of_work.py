"""
SQLAlchemy-реализация Unit of Work.

Эдинственное место в приложении, где вызываются session.commit() и session.rollback().
Все репозитории получают ту же самую сессию — все изменения фиксируются атомарно.

Использование (в domain/application сервисах через DI):

    async def some_use_case(uow: IUnitOfWork) -> ...
        async with uow:
            doc = await uow.documents.get_by_id(doc_id)
            doc.status = DocumentStatus.READY
            await uow.commit()

Правило:
  - Никогда не вызывайте session.commit() внутри репозиториев.
  - Никогда не вызывайте uow.commit() более одного раза за операцию
    (исключение — Celery-задачи с промежуточными чекпоинтами: каждый чекпоинт
    создаёт новый `async with uow` блок).
"""
from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.audit_log_repository import AuditLogRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """Конкретный UoW поверх AsyncSession.

    Создаётся per-request через FastAPI Depends (см. core/dependencies.py).
    Каждая HTTP-операция — одна транзакция. Celery-задачи создают свой экземпляр
    через isolated_db_session() (см. infrastructure/db/session.py).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Репозитории публичные — сервисы обращаются через uow.documents, uow.suggestions …
        self.documents = DocumentRepository(session)
        self.suggestions = SuggestionRepository(session)
        self.jobs = AnalysisJobRepository(session)
        self.audit = AuditLogRepository(session)

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
        # Сессия закрывается владельцем (FastAPI DI / isolated_db_session),
        # поэтому здесь мы только откатываем при ошибке и не трогаем close().

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
