"""
Бизнес-логика регистрации и входа.

Refresh-токены ротируются: jti хранится в RefreshTokenStore, при обновлении
старый jti атомарно отзывается, при logout — отзывается текущий.
"""

from __future__ import annotations

import uuid

from app.domain.exceptions import (
    AccountTemporarilyLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.domain.interfaces.entities import UserProtocol
from app.domain.interfaces.security import (
    ILoginThrottle,
    IPasswordHasher,
    IRefreshTokenStore,
    ITokenIssuer,
)
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.policies import PasswordPolicy


class AuthService:
    def __init__(
        self,
        uow: IUnitOfWork,
        password_hasher: IPasswordHasher,
        jwt_handler: ITokenIssuer,
        rate_limiter: ILoginThrottle,
        refresh_store: IRefreshTokenStore,
        password_policy: PasswordPolicy = PasswordPolicy(),
    ):
        self._uow = uow
        self._hasher = password_hasher
        self._jwt = jwt_handler
        self._rate_limiter = rate_limiter
        self._refresh_store = refresh_store
        self._password_policy = password_policy

    async def register(self, email: str, password: str) -> UserProtocol:
        self._password_policy.validate(password)
        async with self._uow:
            existing = await self._uow.users.get_by_email(email)
            if existing is not None:
                raise EmailAlreadyRegisteredError(f"Email {email} уже зарегистрирован")
            user = await self._uow.users.create_from_credentials(email, self._hasher.hash(password))
            await self._uow.commit()
        return user

    async def authenticate(
        self, email: str, password: str, client_ip: str | None = None
    ) -> tuple[str, str, int, int]:
        is_locked, retry_after = await self._rate_limiter.is_locked(email, client_ip)
        if is_locked:
            raise AccountTemporarilyLockedError(retry_after)

        # Пароль, не проходящий политику, не мог быть сохранён — проверять его
        # хэшированием незачем, а bcrypt на длинном пароле падает.
        if not self._password_policy.is_satisfied_by(password):
            await self._rate_limiter.register_failure(email, client_ip)
            raise InvalidCredentialsError("Неверный email или пароль")

        async with self._uow:
            user = await self._uow.users.get_by_email(email)
        if user is None:
            # Хэшируем и для несуществующего email, чтобы по времени ответа
            # нельзя было понять, зарегистрирован ли адрес.
            self._hasher.hash(password)
            await self._rate_limiter.register_failure(email, client_ip)
            raise InvalidCredentialsError("Неверный email или пароль")
        if not self._hasher.verify(password, user.password_hash):
            await self._rate_limiter.register_failure(email, client_ip)
            raise InvalidCredentialsError("Неверный email или пароль")

        await self._rate_limiter.reset(email, client_ip)
        access_token, expires_in = self._jwt.create_access_token(user.id, user.role)
        refresh_token, refresh_expires_in, jti = self._jwt.create_refresh_token(user.id, user.role)
        await self._refresh_store.save(jti, user.id, refresh_expires_in)
        return access_token, refresh_token, expires_in, refresh_expires_in

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str, int, int]:
        payload = self._jwt.decode_refresh_token(refresh_token)
        jti = payload.get("jti")
        if not jti or not await self._refresh_store.revoke_if_valid(jti):
            raise InvalidCredentialsError("Refresh-токен недействителен или уже использован")

        async with self._uow:
            user = await self._uow.users.get_by_id(uuid.UUID(payload["sub"]))
        if user is None:
            raise InvalidCredentialsError("Пользователь не найден")

        access_token, expires_in = self._jwt.create_access_token(user.id, user.role)
        new_refresh_token, refresh_expires_in, new_jti = self._jwt.create_refresh_token(
            user.id, user.role
        )
        await self._refresh_store.save(new_jti, user.id, refresh_expires_in)
        return access_token, new_refresh_token, expires_in, refresh_expires_in

    async def logout(self, refresh_token: str) -> None:
        """Отозвать refresh-токен при явном logout."""
        try:
            payload = self._jwt.decode_refresh_token(refresh_token)
        except InvalidTokenError:
            # Просроченный или невалидный токен отзывать нечего — logout всё равно успешен.
            return
        jti = payload.get("jti")
        if jti:
            await self._refresh_store.revoke(jti)
