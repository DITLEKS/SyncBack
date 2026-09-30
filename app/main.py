"""Точка входа FastAPI-приложения."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.routers.analysis_jobs import router as analysis_jobs_router
from app.api.v1.routers.analysis_jobs_bulk import router as analysis_jobs_bulk_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.dashboard import router as dashboard_router
from app.api.v1.routers.documents import router as documents_router
from app.api.v1.routers.documents_global import router as documents_global_router
from app.api.v1.routers.editor import router as editor_router
from app.api.v1.routers.my_documents import router as my_documents_router
from app.api.v1.routers.projects import router as projects_router
from app.api.v1.routers.sources import router as sources_router
from app.api.v1.routers.sse import init_sse_broker, shutdown_sse_broker
from app.api.v1.routers.sse import router as sse_router
from app.api.v1.routers.suggestions import router as suggestions_router
from app.api.v1.routers.system import router as system_router
from app.core.body_size_limit_middleware import BodySizeLimitMiddleware
from app.core.config import get_settings
from app.core.correlation_middleware import CorrelationIdMiddleware
from app.core.limiter import limiter
from app.core.logging_setup import configure_logging
from app.domain.exceptions import (
    AnalysisJobNotFoundError,
    DocumentNotFoundError,
    DomainError,
    ProjectNotFoundError,
    SourceNotFoundError,
    SuggestionNotFoundError,
)

logger = logging.getLogger("syncscribe.main")

# M-2: NotFound-исключения, которые должны возвращать 404 (не 400).
_NOT_FOUND_EXCEPTIONS = (
    DocumentNotFoundError,
    ProjectNotFoundError,
    SourceNotFoundError,
    SuggestionNotFoundError,
    AnalysisJobNotFoundError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info(
        "Запуск SyncScribe backend",
        extra={"env": settings.env, "llm_provider": settings.llm_provider},
    )

    await init_sse_broker(
        redis_url=settings.redis_url,
        channel=settings.redis_sse_channel,
    )
    logger.info("SSE broker инициализирован", extra={"channel": settings.redis_sse_channel})

    try:
        yield
    finally:
        await shutdown_sse_broker()
        logger.info("SSE broker остановлен")
        logger.info("Остановка SyncScribe backend")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="SyncScribe API",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # slowapi limiter — до регистрации роутеров.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — первый middleware, до любых других.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # H-3: грубая защита от слишком больших request body до разбора multipart.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    # M-2: NotFound-подклассы DomainError → 404 (должны быть ПЕРЕД общим DomainError handler).
    @app.exception_handler(_NOT_FOUND_EXCEPTIONS[0])
    @app.exception_handler(_NOT_FOUND_EXCEPTIONS[1])
    @app.exception_handler(_NOT_FOUND_EXCEPTIONS[2])
    @app.exception_handler(_NOT_FOUND_EXCEPTIONS[3])
    @app.exception_handler(_NOT_FOUND_EXCEPTIONS[4])
    async def not_found_error_handler(request, exc) -> JSONResponse:
        logger.warning("Ресурс не найден", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # Общий обработчик DomainError → 400 (после специфичных 404-handlers).
    @app.exception_handler(DomainError)
    async def domain_error_handler(request, exc: DomainError) -> JSONResponse:
        logger.warning("Необработанная доменная ошибка", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health_check() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(my_documents_router, prefix="/api/v1")
    app.include_router(documents_global_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(analysis_jobs_router, prefix="/api/v1")
    app.include_router(analysis_jobs_bulk_router, prefix="/api/v1")
    app.include_router(suggestions_router, prefix="/api/v1")
    app.include_router(editor_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(sse_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")

    return app


app = create_app()
