"""
Выпуск и проверка JWT access-токенов.
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
