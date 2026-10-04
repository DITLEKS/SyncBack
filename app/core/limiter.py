"""Общий экземпляр slowapi Limiter для всего приложения.

Лимит на конкретный endpoint задаётся декоратором:
    @limiter.limit("5/minute")
    async def register(request: Request, ...): ...

Счётчики хранятся по settings.rate_limit_storage_uri: в проде это Redis, иначе
каждый процесс gunicorn считал бы лимиты отдельно. Если Redis недоступен,
slowapi временно переходит на память процесса, а не отключает лимиты.

get_remote_address берёт адрес из request.client, который за доверенным прокси
должен подставляться из X-Forwarded-For (см. ProxyHeadersMiddleware в main.py).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=get_settings().rate_limit_storage_uri,
    in_memory_fallback_enabled=True,
    default_limits=[],
    headers_enabled=True,
)
