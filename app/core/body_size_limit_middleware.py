"""Ограничение размера тела запроса до разбора multipart.

Заголовок Content-Length проверяется сразу. Тело без заголовка (chunked) или с
заниженным заголовком считается по мере чтения: как только принятый объём
превышает лимит, чтение обрывается ответом 413, и Starlette не успевает записать
во временный файл больше лимита. Точный лимит на сам файл проверяется при
приёме загрузки; здесь допускается запас на служебные поля multipart.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

_MULTIPART_OVERHEAD_BYTES = 1 * 1024 * 1024


class _BodyTooLarge(HTTPException):
    """HTTPException, чтобы FastAPI не превратил обрыв чтения тела в 400."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=413, detail=detail)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        limit = settings.max_upload_size_bytes + _MULTIPART_OVERHEAD_BYTES
        detail = f"Тело запроса превышает допустимый размер ({settings.max_upload_size_mb} МБ)"

        if _declared_length(scope) > limit:
            await JSONResponse(status_code=413, content={"detail": detail})(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge(detail)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await JSONResponse(status_code=413, content={"detail": detail})(scope, receive, send)


def _declared_length(scope: Scope) -> int:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0
