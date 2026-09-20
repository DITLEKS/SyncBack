"""Служебные эндпоинты диагностики окружения и capabilities."""
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.schemas.system import CapabilitiesResponse
from app.core.config import Settings, get_settings
from app.core.dependencies import get_llm_client_instance
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


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    settings: Settings = Depends(get_settings),
) -> CapabilitiesResponse:
    """Возвращает константы, которые фронт не должен хардкодить.

    Не требует авторизации — используется при инициализации приложения.
    """
    return CapabilitiesResponse(
        supported_formats=_SUPPORTED_FORMATS,
        unsupported_formats=_UNSUPPORTED_FORMATS,
        max_file_size_mb=settings.max_upload_size_mb,
    )
