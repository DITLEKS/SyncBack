"""
Общий экземпляр slowapi Limiter для всего приложения.

Использование:
    from app.core.limiter import limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi import _rate_limit_exceeded_handler

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

Лимит на конкретный endpoint задаётся декоратором:
    @limiter.limit("5/minute")
    async def register(request: Request, ...): ...
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],          # глобальный лимит не выставляем — только точечно
    headers_enabled=True,       # X-RateLimit-* заголовки в ответе
)
