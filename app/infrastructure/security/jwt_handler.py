"""
Выпуск и проверка JWT access/refresh-токенов.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings, get_settings
from app.domain.exceptions import InvalidTokenError


class JWTHandler:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def create_access_token(self, user_id: uuid.UUID, role: str) -> tuple[str, int]:
        now = datetime.now(UTC)
        expires_in = self._settings.jwt_access_token_expire_minutes * 60
        payload = {
            "sub": str(user_id),
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        }
        token = jwt.encode(payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm)
        return token, expires_in

    def create_refresh_token(self, user_id: uuid.UUID, role: str) -> tuple[str, int]:
        now = datetime.now(UTC)
        expires_in = 30 * 24 * 60 * 60
        payload = {
            "sub": str(user_id),
            "role": role,
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        }
        token = jwt.encode(payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm)
        return token, expires_in

    def decode_token(self, token: str) -> dict:
        try:
            return jwt.decode(token, self._settings.jwt_secret, algorithms=[self._settings.jwt_algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise InvalidTokenError("Токен истёк") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError("Невалидный токен") from exc

    def decode_access_token(self, token: str) -> dict:
        payload = self.decode_token(token)
        if payload.get("type") != "access":
            raise InvalidTokenError("Ожидался access token")
        return payload

    def decode_refresh_token(self, token: str) -> dict:
        payload = self.decode_token(token)
        if payload.get("type") != "refresh":
            raise InvalidTokenError("Ожидался refresh token")
        return payload
