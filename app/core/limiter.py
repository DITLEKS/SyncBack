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

M-4: БЕЗОПАСНОСТЬ X-Forwarded-For
─────────────────────────────────
get_remote_address читает X-Forwarded-For без проверки источника.
Если приложение стоит за доверенным reverse proxy (nginx, Traefik),
добавьте в main.py перед CORSMiddleware:

    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="127.0.0.1")

Тогда Starlette перезапишет request.client.host реальным IP из
X-Forwarded-For, только если запрос пришёл от доверенного прокси,
и rate limit нельзя будет обойти подменой заголовка.

В локальной разработке (env=local) это не критично — прокси нет.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # глобальный лимит не выставляем — только точечно
    headers_enabled=True,  # X-RateLimit-* заголовки в ответе
)
