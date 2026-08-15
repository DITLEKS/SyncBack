"""
Прокидывает/генерирует X-Request-ID для каждого HTTP-запроса в contextvar.

Путь в репозитории: app/core/correlation_middleware.py

ИСПРАВЛЕНО: входящий X-Request-ID больше не используется "как есть". Раньше
произвольное значение заголовка от клиента копировалось напрямую в заголовок
ответа (response.headers[...] = request_id). Если клиент присылал значение с
запрещёнными для HTTP-заголовков символами (например, перевод строки или другие
управляющие байты), запись такого значения в заголовок ответа вызывала
необработанное исключение на уровне ASGI/h11 — то есть любой запрос с "кривым"
X-Request-ID превращал ответ сервера в 500 без аутентификации (DoS-вектор).
Теперь входящее значение валидируется по безопасному шаблону и ограничивается по
длине; при несоответствии генерируется новый UUID, как и раньше при отсутствии
заголовка.
"""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_setup import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 128
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,%d}$" % _MAX_REQUEST_ID_LENGTH)


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
