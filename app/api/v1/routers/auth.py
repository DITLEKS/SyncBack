"""
Роутер регистрации/логина/текущего пользователя.

M-block: добавлен rate limit 5/minute на POST /register (защита от
массовых регистраций) и 20/minute на POST /login (H-3: защита от
перебора паролей до достижения lockout на уровне сервиса).
Лимит применяется по IP клиента; при превышении возвращается HTTP 429
с заголовком Retry-After.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi.errors import RateLimitExceeded  # noqa: F401  — re-exported для тестов

from app.api.deps import get_current_user
from app.api.schemas.auth import (
    RefreshTokenRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.core.dependencies import get_auth_service
from app.core.limiter import limiter
from app.domain.exceptions import (
    AccountTemporarilyLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.domain.services.auth_service import AuthService
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: UserRegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """Регистрация нового пользователя.

    Rate limit: 5 запросов в минуту с одного IP.
    """
    try:
        user = await auth_service.register(payload.email, payload.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("20/minute")
async def login(
    request: Request,
    payload: UserLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Аутентификация пользователя.

    H-3: Rate limit 20/minute по IP — защищает от перебора паролей
    до достижения lockout на уровне сервиса (login_max_attempts).
    """
    try:
        access_token, refresh_token, expires_in, refresh_expires_in = await auth_service.authenticate(
            payload.email, payload.password
        )
    except AccountTemporarilyLockedError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        access_token, refresh_token, expires_in, refresh_expires_in = await auth_service.refresh_access_token(
            payload.refresh_token
        )
    except (InvalidTokenError, InvalidCredentialsError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
