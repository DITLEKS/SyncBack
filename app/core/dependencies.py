"""
DI-фабрики FastAPI.

После введения UoW:
  - Сервисы получают IUnitOfWork вместо набора репозиториев.
  - SqlAlchemyUnitOfWork создаётся per-request через get_db_session().
  - Конкретные репозитории больше не инстанцируются в этом файле напрямую
    (они создаются внутри SqlAlchemyUnitOfWork).
"""

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
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
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
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
    """Per-request Unit of Work.

    Один экземпляр на HTTP-запрос — все операции одного запроса
    видят одно и то же состояние сессии и фиксируются одним commit().
    """
    return SqlAlchemyUnitOfWork(session)


# ---------------------------------------------------------------------------
# Services that use UoW
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


# ---------------------------------------------------------------------------
# Services that still use individual repos (next iteration)
# ---------------------------------------------------------------------------

async def get_project_service(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectService:
    return ProjectService(
        project_repository=ProjectRepository(session),
        file_storage=_get_minio_storage(),
    )


async def get_document_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> DocumentService:
    return DocumentService(
        document_repository=DocumentRepository(session),
        file_storage=_get_minio_storage(),
        parser_registry=_get_parser_registry(),
        settings=settings,
    )


async def get_source_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SourceService:
    return SourceService(
        source_repository=SourceRepository(session),
        file_storage=_get_minio_storage(),
        settings=settings,
    )


async def get_document_export_service(
    session: AsyncSession = Depends(get_db_session),
) -> DocumentExportService:
    return DocumentExportService(
        suggestion_repository=SuggestionRepository(session),
        file_storage=_get_minio_storage(),
        exporter_registry=_get_exporter_registry(),
        parser_registry=_get_parser_registry(),
    )


async def get_dashboard_service(
    session: AsyncSession = Depends(get_db_session),
) -> DashboardService:
    return DashboardService(dashboard_repository=DashboardRepository(session))


async def get_login_rate_limiter() -> LoginRateLimiter:
    redis = await get_redis_client()
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


def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> UserRepository:
    return UserRepository(session)


def get_project_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRepository:
    return ProjectRepository(session)


def get_llm_client_instance(
    settings: Settings = Depends(get_settings),
):
    return get_llm_client(settings)
