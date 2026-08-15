"""Служебные эндпоинты диагностики окружения."""
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.dependencies import get_llm_client_instance
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm-health")
async def llm_health(
    current_user: User = Depends(get_current_user),
    llm_client=Depends(get_llm_client_instance),
    settings: Settings = Depends(get_settings),
) -> dict:
    is_healthy = await llm_client.health_check()
    return {"provider": settings.llm_provider, "healthy": is_healthy}
