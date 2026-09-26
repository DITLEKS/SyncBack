"""
Тест: SqlAlchemyUnitOfWork полностью реализует ABC IUnitOfWork.

Добавление нового @abstractmethod в IUnitOfWork безсильно приведёт
к TypeError: Can't instantiate abstract class SqlAlchemyUnitOfWork
with abstract methods ... — этот тест поймает регрессию сразу.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.interfaces.repositories import (
    IAnalysisJobRepository,
    IAuditLogRepository,
    IDocumentRepository,
    IProjectRepository,
    ISourceRepository,
    ISuggestionRepository,
    IUserRepository,
)
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def _make_mock_session() -> AsyncMock:
    """AsyncMock минимальной AsyncSession для конструктора UoW."""
    session = AsyncMock()
    session.commit   = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh  = AsyncMock()
    return session


class TestSqlAlchemyUoWABCCompleteness:
    """SqlAlchemyUnitOfWork должен реализовывать все абстрактные методы IUnitOfWork."""

    def test_instantiation_does_not_raise_type_error(self):
        """Главный тест: TypeError не возникает при инстанцировании.

        Если какой-то @abstractmethod в IUnitOfWork не реализован,
        Python бросит TypeError именно здесь.
        """
        session = _make_mock_session()
        # Не должно бросать TypeError.
        uow = SqlAlchemyUnitOfWork(session)
        assert uow is not None

    def test_is_iunitofwork_instance(self):
        """SqlAlchemyUnitOfWork является экземпляром IUnitOfWork."""
        uow = SqlAlchemyUnitOfWork(_make_mock_session())
        assert isinstance(uow, IUnitOfWork)

    def test_repository_attributes_present(self):
        """Все ожидаемые репозитории-атрибуты присутствуют на инстанце."""
        uow = SqlAlchemyUnitOfWork(_make_mock_session())
        assert hasattr(uow, "documents")
        assert hasattr(uow, "suggestions")
        assert hasattr(uow, "jobs")
        assert hasattr(uow, "audit")
        assert hasattr(uow, "projects")
        assert hasattr(uow, "sources")
        assert hasattr(uow, "users")

    def test_repository_types(self):
        """Репозитории имеют ожидаемые типы (smoke check)."""
        uow = SqlAlchemyUnitOfWork(_make_mock_session())
        assert isinstance(uow.documents,   IDocumentRepository)
        assert isinstance(uow.suggestions, ISuggestionRepository)
        assert isinstance(uow.jobs,        IAnalysisJobRepository)
        assert isinstance(uow.audit,       IAuditLogRepository)
        assert isinstance(uow.projects,    IProjectRepository)
        assert isinstance(uow.sources,     ISourceRepository)
        assert isinstance(uow.users,       IUserRepository)

    def test_abstract_methods_covered(self):
        """Проверить, что в IUnitOfWork нет нереализованных абстрактных методов.

        Если в IUnitOfWork добавляют @abstractmethod, но 'SQLалхемияУоЩ' не
        реализует его, __abstractmethods__ будет непустым, и конструктор бросит TypeError.
        Этот тест явно проверяет наличие таких методов.
        """
        missing = getattr(SqlAlchemyUnitOfWork, "__abstractmethods__", frozenset())
        assert missing == frozenset(), (
            f"SqlAlchemyUnitOfWork не реализует абстрактные методы IUnitOfWork: {missing}"
        )
