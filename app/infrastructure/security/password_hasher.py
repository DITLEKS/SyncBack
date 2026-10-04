"""Хэширование паролей через bcrypt.

Библиотека bcrypt используется напрямую: passlib не развивается с 2020 года и
не совместим с bcrypt ≥ 4.1. Формат хэша тот же ($2b$), поэтому записи,
созданные через passlib, проверяются без миграции.

bcrypt с 12 раундами занимает порядка 100–300 мс процессорного времени, поэтому
вычисление уходит в поток: иначе каждый вход блокировал бы event loop для всех
остальных запросов.
"""

import asyncio

import bcrypt

_ROUNDS = 12


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(_ROUNDS)).decode("ascii")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # Повреждённый или не-bcrypt хэш в БД — считаем пароль неверным, а не падаем.
        return False


class PasswordHasher:
    async def hash(self, plain_password: str) -> str:
        return await asyncio.to_thread(hash_password, plain_password)

    async def verify(self, plain_password: str, password_hash: str) -> bool:
        return await asyncio.to_thread(verify_password, plain_password, password_hash)
