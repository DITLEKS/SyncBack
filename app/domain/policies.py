"""Ограничения предметной области, которые задаются конфигурацией.

Домен не читает настройки приложения напрямую: значения передаются сюда
при сборке сервисов, а сервисы работают с готовыми объектами.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.exceptions import InvalidPasswordError

_MIB = 1024 * 1024


@dataclass(frozen=True)
class UploadLimits:
    """Лимит размера загружаемого файла или текста источника."""

    max_size_bytes: int

    @classmethod
    def from_megabytes(cls, megabytes: int) -> UploadLimits:
        return cls(max_size_bytes=megabytes * _MIB)

    @property
    def max_size_mb(self) -> int:
        return self.max_size_bytes // _MIB

    def exceeded_by(self, size_bytes: int) -> bool:
        return size_bytes > self.max_size_bytes


@dataclass(frozen=True)
class PasswordPolicy:
    """Требования к паролю при регистрации.

    max_bytes — ограничение bcrypt: алгоритм учитывает только первые 72 байта,
    поэтому более длинный пароль либо молча обрезается, либо отвергается
    библиотекой. Проверяем заранее и объясняем пользователю.
    """

    min_length: int = 8
    max_length: int = 128
    max_bytes: int = 72

    def is_satisfied_by(self, password: str) -> bool:
        try:
            self.validate(password)
        except InvalidPasswordError:
            return False
        return True

    def validate(self, password: str) -> None:
        if len(password) < self.min_length:
            raise InvalidPasswordError(f"Пароль короче {self.min_length} символов")
        if len(password) > self.max_length:
            raise InvalidPasswordError(f"Пароль длиннее {self.max_length} символов")
        if len(password.encode("utf-8")) > self.max_bytes:
            raise InvalidPasswordError(
                f"Пароль занимает больше {self.max_bytes} байт в UTF-8; "
                "используйте более короткий пароль"
            )
