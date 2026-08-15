"""
Простые фабрики зависимостей поверх встроенного DI FastAPI (Depends).
"""
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.auth_service import AuthService
from app.domain.services.document_export_service import DocumentExportService
from app.domain.services.document_service import DocumentService
from app.domain.services.project_service import ProjectService
from app.domain.services.source_service import SourceService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.cache.redis_client import get_redis_client
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.audit_log_repository import AuditLogRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository
from app.infrastructure.db.repositories.user_repository import UserRepository
from app.infrastructure.db.session import get_db_session
from app.infrastructure.exporters.exporter_registry import DocumentExporterRegistry
from app.infrastructure.llm.factory import get_llm_client
from app.infrastructure.security.jwt_handler import JWTHandler
from app.infrastructure.security.login_rate_limiter import LoginRateLimiter
from app.infrastructure.security.password_hasher import PasswordHasher
from app.infrastructure.storage.minio_storage import MinioStorage


def get_password_hasher() -> PasswordHasher:
    return PasswordHasher()


def get_jwt_handler() -> JWTHandler:
    return JWTHandler()


def get_file_storage() -> MinioStorage:
    return MinioStorage()


def get_llm_client_instance():
    return get_llm_client()


def get_exporter_registry() -> DocumentExporterRegistry:
    return DocumentExporterRegistry()


def get_user_repository(session: AsyncSession = Depends(get_db_session)) -> UserRepository:
    return UserRepository(session)


def get_project_repository(session: AsyncSession = Depends(get_db_session)) -> ProjectRepository:
    return ProjectRepository(session)


def get_document_repository(session: AsyncSession = Depends(get_db_session)) -> DocumentRepository:
    return DocumentRepository(session)


def get_source_repository(session: AsyncSession = Depends(get_db_session)) -> SourceRepository:
    return SourceRepository(session)


def get_audit_log_repository(session: AsyncSession = Depends(get_db_session)) -> AuditLogRepository:
    return AuditLogRepository(session)


def get_analysis_job_repository(session: AsyncSession = Depends(get_db_session)) -> AnalysisJobRepository:
    return AnalysisJobRepository(session)


def get_suggestion_repository(session: AsyncSession = Depends(get_db_session)) -> SuggestionRepository:
    return SuggestionRepository(session)


def get_login_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter(get_redis_client(), get_settings())


def get_auth_service(
    user_repository: UserRepository = Depends(get_user_repository),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
    jwt_handler: JWTHandler = Depends(get_jwt_handler),
    rate_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
) -> AuthService:
    return AuthService(user_repository, password_hasher, jwt_handler, rate_limiter)


def get_project_service(project_repository: ProjectRepository = Depends(get_project_repository)) -> ProjectService:
    return ProjectService(project_repository)


def get_document_service(
    document_repository: DocumentRepository = Depends(get_document_repository),
    file_storage: MinioStorage = Depends(get_file_storage),
) -> DocumentService:
    return DocumentService(document_repository, file_storage)


def get_source_service(
    source_repository: SourceRepository = Depends(get_source_repository),
    file_storage: MinioStorage = Depends(get_file_storage),
) -> SourceService:
    return SourceService(source_repository, file_storage)


def get_audit_log_service(audit_log_repository: AuditLogRepository = Depends(get_audit_log_repository)) -> AuditLogService:
    return AuditLogService(audit_log_repository)


def get_analysis_job_service(
    analysis_job_repository: AnalysisJobRepository = Depends(get_analysis_job_repository),
    document_repository: DocumentRepository = Depends(get_document_repository),
) -> AnalysisJobService:
    return AnalysisJobService(analysis_job_repository, document_repository)


def get_suggestion_service(
    suggestion_repository: SuggestionRepository = Depends(get_suggestion_repository),
    document_repository: DocumentRepository = Depends(get_document_repository),
) -> SuggestionService:
    return SuggestionService(suggestion_repository, document_repository)


def get_document_export_service(
    file_storage: MinioStorage = Depends(get_file_storage),
    exporter_registry: DocumentExporterRegistry = Depends(get_exporter_registry),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> DocumentExportService:
    return DocumentExportService(file_storage, exporter_registry, suggestion_service)
