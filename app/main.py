"""Точка входа FastAPI-приложения."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1.routers.analysis_jobs import router as analysis_jobs_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.documents import router as documents_router
from app.api.v1.routers.projects import router as projects_router
from app.api.v1.routers.sources import router as sources_router
from app.api.v1.routers.suggestions import router as suggestions_router
from app.api.v1.routers.system import router as system_router
from app.core.config import get_settings
from app.core.correlation_middleware import CorrelationIdMiddleware
from app.core.logging_setup import configure_logging
from app.domain.exceptions import DomainError

logger = logging.getLogger("syncscribe.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("Запуск SyncScribe backend", extra={"env": settings.env, "llm_provider": settings.llm_provider})
    yield
    logger.info("Остановка SyncScribe backend")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="SyncScribe API", version="0.1.0", debug=settings.debug, lifespan=lifespan)

    app.add_middleware(CorrelationIdMiddleware)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request, exc: DomainError) -> JSONResponse:
        logger.warning("Необработанная доменная ошибка", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health_check() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(analysis_jobs_router, prefix="/api/v1")
    app.include_router(suggestions_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")

    return app


app = create_app()
