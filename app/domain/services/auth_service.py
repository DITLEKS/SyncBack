"""
Бизнес-логика регистрации и входа.
"""

import uuid

from app.domain.exceptions import (
    AccountTemporarilyLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)
from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.user_repository import UserRepository
from app.infrastructure.security.jwt_handler import JWTHandler
from app.infrastructure.security.login_rate_limiter import LoginRateLimiter
from app.infrastructure.security.password_hasher import PasswordHasher


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        password_hasher: PasswordHasher,
        jwt_handler: JWTHandler,
        rate_limiter: LoginRateLimiter,
    ):
        self._users = user_repository
        self._hasher = password_hasher
        self._jwt = jwt_handler
        self._rate_limiter = rate_limiter

    async def register(self, email: str, password: str) -> User:
        existing = await self._users.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyRegisteredError(f"Email {email} уже зарегистрирован")

        user = User(email=email, password_hash=self._hasher.hash(password), role=UserRole.USER)
        return await self._users.create(user)

    async def authenticate(self, email: str, password: str) -> tuple[str, str, int, int]:
        is_locked, retry_after = await self._rate_limiter.is_locked(email)
        if is_locked:
            raise AccountTemporarilyLockedError(retry_after)

        user = await self._users.get_by_email(email)
        if user is None or not self._hasher.verify(password, user.password_hash):
            await self._rate_limiter.register_failure(email)
            raise InvalidCredentialsError("Неверный email или пароль")

        await self._rate_limiter.reset(email)
        access_token, expires_in = self._jwt.create_access_token(user.id, user.role.value)
        refresh_token, refresh_expires_in = self._jwt.create_refresh_token(user.id, user.role.value)
        return access_token, refresh_token, expires_in, refresh_expires_in

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str, int, int]:
        payload = self._jwt.decode_refresh_token(refresh_token)
        user = await self._users.get_by_id(uuid.UUID(payload["sub"]))
        if user is None:
            raise InvalidCredentialsError("Пользователь не найден")

        access_token, expires_in = self._jwt.create_access_token(user.id, user.role.value)
        new_refresh_token, refresh_expires_in = self._jwt.create_refresh_token(user.id, user.role.value)
        return access_token, new_refresh_token, expires_in, refresh_expires_in
