"""
DI-фабрики зависимостей FastAPI.

ProjectService принимает file_storage — нужен для delete_project().
DashboardService подключён с реальным DashboardRepository (CR-2).
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
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.audit_log_repository import AuditLogRepository
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository
from app.infrastructure.db.session import get_db_session
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


def get_project_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRepository:
    return ProjectRepository(session)


def get_user_repository(session: AsyncSession = Depends(get_db_session)):
    from app.infrastructure.db.repositories.user_repository import UserRepository

    return UserRepository(session)


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


async def get_analysis_job_service(
    session: AsyncSession = Depends(get_db_session),
) -> AnalysisJobService:
    return AnalysisJobService(
        analysis_job_repository=AnalysisJobRepository(session),
        document_repository=DocumentRepository(session),
    )


async def get_suggestion_service(
    session: AsyncSession = Depends(get_db_session),
) -> SuggestionService:
    return SuggestionService(
        suggestion_repository=SuggestionRepository(session),
        document_repository=DocumentRepository(session),
    )


async def get_audit_log_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogService:
    return AuditLogService(audit_log_repository=AuditLogRepository(session))


async def get_document_export_service(
    session: AsyncSession = Depends(get_db_session),
) -> DocumentExportService:
    return DocumentExportService(
        suggestion_repository=SuggestionRepository(session),
        file_storage=_get_minio_storage(),
        exporter_registry=_get_exporter_registry(),
        parser_registry=_get_parser_registry(),
    )


def get_llm_client_instance(settings: Settings = Depends(get_settings)):
    return get_llm_client(settings)


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
    from app.infrastructure.db.repositories.user_repository import UserRepository

    return AuthService(UserRepository(session), PasswordHasher(), JWTHandler(settings), rate_limiter)


async def get_dashboard_service(
    session: AsyncSession = Depends(get_db_session),
) -> DashboardService:
    return DashboardService(dashboard_repository=DashboardRepository(session))
