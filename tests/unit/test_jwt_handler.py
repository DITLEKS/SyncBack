"""
Проверяем, что JWT имеет срок жизни (expiry), а не выпускается бессрочным, и что
просроченный/невалидный токен корректно приводит к InvalidTokenError, а не к тихому
пропуску проверки.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.exceptions import InvalidTokenError
from app.infrastructure.security.jwt_handler import JWTHandler


def _make_settings(expire_minutes: int = 60):
    return SimpleNamespace(jwt_secret="test-secret", jwt_algorithm="HS256", jwt_access_token_expire_minutes=expire_minutes)


def test_create_and_decode_token_roundtrip():
    handler = JWTHandler(_make_settings(expire_minutes=60))
    user_id = uuid.uuid4()

    token, expires_in = handler.create_access_token(user_id, "user")

    assert expires_in == 60 * 60
    payload = handler.decode_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "user"
    assert "exp" in payload  # токен обязательно содержит срок истечения


def test_decode_expired_token_raises_invalid_token_error():
    # Отрицательное время жизни гарантированно даёт уже истёкший токен
    handler = JWTHandler(_make_settings(expire_minutes=-1))
    token, _ = handler.create_access_token(uuid.uuid4(), "user")

    with pytest.raises(InvalidTokenError):
        handler.decode_token(token)


def test_decode_garbage_token_raises_invalid_token_error():
    handler = JWTHandler(_make_settings())
    with pytest.raises(InvalidTokenError):
        handler.decode_token("this-is-not-a-jwt")


def test_decode_token_signed_with_different_secret_raises():
    handler_a = JWTHandler(_make_settings())
    handler_b = JWTHandler(SimpleNamespace(jwt_secret="another-secret", jwt_algorithm="HS256", jwt_access_token_expire_minutes=60))
    token, _ = handler_a.create_access_token(uuid.uuid4(), "user")

    with pytest.raises(InvalidTokenError):
        handler_b.decode_token(token)
