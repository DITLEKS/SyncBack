"""
Хэширование паролей через bcrypt (passlib).
"""

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordHasher:
    @staticmethod
    def hash(plain_password: str) -> str:
        return _pwd_context.hash(plain_password)

    @staticmethod
    def verify(plain_password: str, password_hash: str) -> bool:
        return _pwd_context.verify(plain_password, password_hash)
