"""
Бизнес-логика регистрации и входа.
"""

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

    async def authenticate(self, email: str, password: str) -> tuple[str, int]:
        is_locked, retry_after = await self._rate_limiter.is_locked(email)
        if is_locked:
            raise AccountTemporarilyLockedError(retry_after)

        user = await self._users.get_by_email(email)
        if user is None or not self._hasher.verify(password, user.password_hash):
            await self._rate_limiter.register_failure(email)
            raise InvalidCredentialsError("Неверный email или пароль")

        await self._rate_limiter.reset(email)
        return self._jwt.create_access_token(user.id, user.role.value)
