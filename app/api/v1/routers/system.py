"""Служебные эндпоинты диагностики окружения и возможностей системы."""
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.schemas.system import CapabilitiesResponse
from app.core.config import Settings, get_settings
from app.core.dependencies import get_llm_client_instance
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/system", tags=["system"])

# Форматы, которые парсер принимает без ошибки
_SUPPORTED_FORMATS = ["pdf", "docx", "txt", "md"]
# Форматы, которые известны, но вернут 400 UnsupportedFormatError
_UNSUPPORTED_FORMATS = ["doc"]


@router.get("/llm-health")
async def llm_health(
    current_user: User = Depends(get_current_user),
    llm_client=Depends(get_llm_client_instance),
    settings: Settings = Depends(get_settings),
) -> dict:
    is_healthy = await llm_client.health_check()
    return {"provider": settings.llm_provider, "healthy": is_healthy}


@router.get("/capabilities")
async def get_capabilities(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Возможности системы для фронтенда — форматы, лимиты, ограничения.

    Фронтенд не должен хардкодить эти параметры — они читаются при старте.
    Endpoint публичный (без авторизации): нужен до логина для инициализации UI.
    """
    supported_formats = [
        f.value
        for f in DocumentFormat
        if f != DocumentFormat.DOC  # .doc не поддерживается парсером
    ]
    return {
        "upload": {
            "max_size_mb": settings.max_upload_size_mb,
            "max_size_bytes": settings.max_upload_size_bytes,
            "supported_formats": supported_formats,
            "supported_mime_types": [
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
                "text/plain",   # .txt
                "text/markdown",  # .md
                "text/x-markdown",
            ],
        },
        "analysis": {
            "idempotency_key_required": False,
            "parallel_jobs_per_document": 1,
        },
        "review": {
            "atomic_save_endpoint": "PUT /projects/{project_id}/documents/{document_id}/review",
            "optimistic_locking": True,
        },
        "export": {
            "supported_formats": ["docx", "txt", "markdown"],
        },
    }
