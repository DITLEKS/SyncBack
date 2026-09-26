"""
DI-фабрики FastAPI.

Все сервисы получают UoW (или отдельные репозитории для AuthService, у которого
нет собственного UoW-метода). Конкретные репозитории НЕ инстансируются в этом
файле напрямую — их создаёт SqlAlchemyUnitOfWork или фабрика сервиса.

H2.2: фабрики по-прежнему создают concrete SQLAlchemy-репозитории,
но передают их сервисам как значения, удовлетворяющие доменным портам.
Типы аннотаций в фабриках оставлены конкретными — FastAPI DI не понимает
Protocol для Depends, зато mypy/pyright проверят, что concrete-репозитории
действительно реализуют порты через @runtime_checkable.

CRIT-D4 (этот раунд):
  get_dashboard_service ранее передавал uow= в DashboardService,
  но DashboardService переключён на IDashboardQueryService (CRIT-D1).
  Исправлено: добавлена фабрика get_dashboard_query_service,
  которая создаёт DashboardRepository(session) — конкретную реализацию
  IDashboardQueryService. get_dashboard_service теперь принимает
  dashboard_qs через Depends(get_dashboard_query_service).
"""

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.auth_service import AuthService
from app.domain.services.dashboard_service import DashboardService
from app.domain.services.document_export_service import DocumentExportService
from app.domain.services.document_service import DocumentService
from app.domain.services.project_service import ProjectService
from app.domain.services.source_service import SourceService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.cache.redis_client import get_redis_client
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.user_repository import UserRepository
from app.infrastructure.db.session import get_db_session
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.exporters.exporter_registry import DocumentExporterRegistry
from app.infrastructure.llm.factory import get_llm_client
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.security.jwt_handler import JWTHandler
from app.infrastructure.security.login_rate_limiter import LoginRateLimiter
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.storage.minio_storage import MinioStorage


# ---------------------------------------------------------------------------
# Singleton-like infrastructure (one instance per process)
# ---------------------------------------------------------------------------

@lru_cache
def _get_minio_storage() -> MinioStorage:
    return MinioStorage(get_settings())


@lru_cache
def _get_parser_registry() -> DocumentParserRegistry:
    return DocumentParserRegistry()


@lru_cache
def _get_exporter_registry() -> DocumentExporterRegistry:
    return DocumentExporterRegistry()


# ---------------------------------------------------------------------------
# Unit of Work (per-request)
# ---------------------------------------------------------------------------

def get_uow(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyUnitOfWork:
    """Пер-request Unit of Work.

    Один экземпляр на HTTP-запрос — все операции одного запроса
    видят одно и то же состояние сессии и фиксируются одним commit().
    """
    return SqlAlchemyUnitOfWork(session)


# ---------------------------------------------------------------------------
# Services — все используют UoW
# ---------------------------------------------------------------------------

def get_suggestion_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> SuggestionService:
    return SuggestionService(uow)


def get_analysis_job_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> AnalysisJobService:
    return AnalysisJobService(uow)


def get_audit_log_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> AuditLogService:
    return AuditLogService(uow)


def get_project_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> ProjectService:
    return ProjectService(
        uow=uow,
        file_storage=_get_minio_storage(),
    )


def get_document_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    settings: Settings = Depends(get_settings),
) -> DocumentService:
    return DocumentService(
        uow=uow,
        file_storage=_get_minio_storage(),
        parser_registry=_get_parser_registry(),
        settings=settings,
    )


def get_source_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    settings: Settings = Depends(get_settings),
) -> SourceService:
    return SourceService(
        uow=uow,
        file_storage=_get_minio_storage(),
        settings=settings,
    )


def get_document_export_service(
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> DocumentExportService:
    return DocumentExportService(
        uow=uow,
        file_storage=_get_minio_storage(),
        exporter_registry=_get_exporter_registry(),
        parser_registry=_get_parser_registry(),
    )


# ---------------------------------------------------------------------------
# Dashboard — read-model инжектируется напрямую, минуя UoW
# ---------------------------------------------------------------------------

def get_dashboard_query_service(
    session: AsyncSession = Depends(get_db_session),
) -> IDashboardQueryService:
    """CRIT-D4: DashboardRepository реализует IDashboardQueryService.

    Инжектируется напрямую (без UoW) — read-model не участвует в транзакциях агрегатов.
    Отдельная сессия от UoW — SELECT-запросы дашборда не мешают write-транзакциям.
    """
    return DashboardRepository(session)


def get_dashboard_service(
    dashboard_qs: IDashboardQueryService = Depends(get_dashboard_query_service),
) -> DashboardService:
    """CRIT-D4: передаём dashboard_qs=, а не uow=.

    DashboardService.__init__ ожидает IDashboardQueryService после CRIT-D1.
    """
    return DashboardService(dashboard_qs=dashboard_qs)


# ---------------------------------------------------------------------------
# Per-request repositories (используются в deps.py для auth/authz)
# ---------------------------------------------------------------------------

def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> UserRepository:
    """Отдельная сессия для UserRepository — Auth-слой вне UoW по дизайну."""
    return UserRepository(session)


def get_project_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRepository:
    """CRIT-NEW-1: фабрика для ProjectRepository в deps.py (get_allowed_project)."""
    return ProjectRepository(session)


# ---------------------------------------------------------------------------
# Auth (UserRepository живёт вне UoW — отдельная сессия по дизайну)
# ---------------------------------------------------------------------------

def get_login_rate_limiter() -> LoginRateLimiter:
    # M-6: get_redis_client() — sync def, await здесь недопустим (TypeError в рантайме).
    redis = get_redis_client()
    settings = get_settings()
    return LoginRateLimiter(
        redis_client=redis,
        max_attempts=settings.login_max_attempts,
        lockout_seconds=settings.login_lockout_seconds,
    )


async def get_auth_service(
    session: AsyncSession = Depends(get_db_session),
    rate_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(
        UserRepository(session),
        PasswordHasher(),
        JWTHandler(settings),
        rate_limiter,
    )


def get_llm_client_instance(
    settings: Settings = Depends(get_settings),
):
    return get_llm_client(settings)
