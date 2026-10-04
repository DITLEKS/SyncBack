"""Роутер регистрации, входа, обновления и отзыва токенов.

Лимиты slowapi считаются по IP клиента и защищают от массовых регистраций
и перебора паролей до срабатывания блокировки на уровне сервиса;
при превышении возвращается 429 с заголовком Retry-After.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi.util import get_remote_address

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
    InvalidPasswordError,
    InvalidTokenError,
)
from app.domain.services.auth_service import AuthService
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    response: Response,
    payload: UserRegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """Регистрация нового пользователя.

    Rate limit: 5 запросов в минуту с одного IP. Параметр response нужен slowapi,
    чтобы записать заголовки X-RateLimit-* в ответ, собранный из pydantic-модели.
    """
    try:
        user = await auth_service.register(payload.email, payload.password)
    except InvalidPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("20/minute")
async def login(
    request: Request,
    response: Response,
    payload: UserLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Аутентификация пользователя."""
    try:
        (
            access_token,
            refresh_token,
            expires_in,
            refresh_expires_in,
        ) = await auth_service.authenticate(
            payload.email, payload.password, client_ip=get_remote_address(request)
        )
    except AccountTemporarilyLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh_tokens(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        (
            access_token,
            refresh_token,
            expires_in,
            refresh_expires_in,
        ) = await auth_service.refresh_access_token(payload.refresh_token)
    except (InvalidTokenError, InvalidCredentialsError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    """Отозвать refresh-токен.

    Ответ одинаковый для действующего, уже отозванного и невалидного токена:
    клиенту достаточно знать, что продолжить сессию этим токеном нельзя.
    Access-токен действует до истечения срока.
    """
    await auth_service.logout(payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
