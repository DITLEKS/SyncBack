"""Служебные эндпоинты диагностики окружения и возможностей системы."""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.schemas.system import (
    AnalysisCapabilities,
    CapabilitiesResponse,
    ExportCapabilities,
    ReviewCapabilities,
    UploadCapabilities,
)
from app.core.config import Settings, get_settings
from app.core.dependencies import get_llm_client_instance
from app.domain.services.document_service import FORMAT_BY_EXTENSION
from app.domain.value_objects import DocumentStatusVO
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm-health")
async def llm_health(
    current_user: User = Depends(require_admin),
    llm_client=Depends(get_llm_client_instance),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Диагностика LLM-провайдера: только для администратора, раскрывает конфигурацию."""
    is_healthy = await llm_client.health_check()
    return {"provider": settings.llm_provider, "healthy": is_healthy}


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(settings: Settings = Depends(get_settings)) -> CapabilitiesResponse:
    """Форматы, лимиты и правила API для фронтенда.

    Публичный: нужен до входа, чтобы настроить загрузку. Значения берутся из тех же
    настроек и таблиц, что проверяют запросы, поэтому не расходятся с поведением.
    """
    document_base = "/api/v1/projects/{project_id}/documents/{document_id}"
    return CapabilitiesResponse(
        upload=UploadCapabilities(
            max_size_mb=settings.max_upload_size_mb,
            max_size_bytes=settings.max_upload_size_bytes,
            document_formats=sorted({f.value for f in FORMAT_BY_EXTENSION.values()}),
            document_extensions=sorted(FORMAT_BY_EXTENSION),
            unsupported_extensions=[".doc"],
        ),
        analysis=AnalysisCapabilities(
            idempotency_header="Idempotency-Key",
            parallel_jobs_per_document=1,
            force_required_for_ready=True,
        ),
        review=ReviewCapabilities(
            atomic_save_endpoint=f"PUT {document_base}/suggestions/review",
            optimistic_locking=True,
            version_header="If-Match",
        ),
        export=ExportCapabilities(
            endpoint=f"GET {document_base}/export",
            same_format_only=True,
            requires_status=DocumentStatusVO.READY.value,
        ),
    )
