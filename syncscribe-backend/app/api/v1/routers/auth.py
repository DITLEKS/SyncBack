"""
Роутер регистрации/логина/текущего пользователя.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.api.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.core.dependencies import get_auth_service
from app.domain.exceptions import (
    AccountTemporarilyLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)
from app.domain.services.auth_service import AuthService
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegisterRequest, auth_service: AuthService = Depends(get_auth_service)) -> UserResponse:
    try:
        user = await auth_service.register(payload.email, payload.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLoginRequest, auth_service: AuthService = Depends(get_auth_service)) -> TokenResponse:
    try:
        access_token, expires_in = await auth_service.authenticate(payload.email, payload.password)
    except AccountTemporarilyLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
