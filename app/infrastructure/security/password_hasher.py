"""Хэширование паролей через bcrypt.

Библиотека bcrypt используется напрямую: passlib не развивается с 2020 года и
не совместим с bcrypt ≥ 4.1. Формат хэша тот же ($2b$), поэтому записи,
созданные через passlib, проверяются без миграции.
"""

import bcrypt

_ROUNDS = 12


class PasswordHasher:
    @staticmethod
    def hash(plain_password: str) -> str:
        return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(_ROUNDS)).decode(
            "ascii"
        )

    @staticmethod
    def verify(plain_password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("ascii"))
        except ValueError:
            # Повреждённый или не-bcrypt хэш в БД — считаем пароль неверным, а не падаем.
            return False
