"""
Ограничение размера тела запроса на уровне middleware — проверяем Content-Length ещё до
того, как FastAPI начнёт разбирать multipart-тело. Это не отменяет точную проверку размера
файла в DocumentService/SourceService (там считается реальный лимит на сам файл, без учёта
служебных полей multipart) — здесь только грубая защита от заведомо слишком больших
запросов, чтобы не тратить память на буферизацию тела, которое всё равно будет отклонено.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

# Запас на служебные поля/границы multipart поверх собственно лимита на файл
_MULTIPART_OVERHEAD_BYTES = 1 * 1024 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        content_length = request.headers.get("content-length")

        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = None

            if size is not None and size > settings.max_upload_size_bytes + _MULTIPART_OVERHEAD_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Тело запроса превышает допустимый размер ({settings.max_upload_size_mb} МБ)"},
                )

        return await call_next(request)
