"""Ограничения предметной области, которые задаются конфигурацией.

Домен не читает настройки приложения напрямую: значения передаются сюда
при сборке сервисов, а сервисы работают с готовыми объектами.
"""

from __future__ import annotations

from dataclasses import dataclass

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
