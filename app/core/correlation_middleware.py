"""
Прокидывает/генерирует X-Request-ID для каждого HTTP-запроса в contextvar.

Путь в репозитории: app/core/correlation_middleware.py

Входящий X-Request-ID не копируется в ответ как есть: значение с управляющими
символами (например, переводом строки) роняло бы запись заголовка на уровне
ASGI/h11 и давало 500 любому неаутентифицированному клиенту. Поэтому значение
проверяется по безопасному шаблону и длине, а при несоответствии или отсутствии
генерируется новый UUID.
"""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_setup import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 128
_SAFE_REQUEST_ID_RE = re.compile(rf"^[A-Za-z0-9._-]{{1,{_MAX_REQUEST_ID_LENGTH}}}$")


def _sanitize_request_id(incoming_id: str | None) -> str:
    if incoming_id and _SAFE_REQUEST_ID_RE.match(incoming_id):
        return incoming_id
    return str(uuid.uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = _sanitize_request_id(incoming_id)
        token = request_id_var.set(request_id)

        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
