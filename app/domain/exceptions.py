"""Доменные исключения SyncBack.

Каждое исключение соответствует одной бизнес-ситуации и конвертируется
в HTTP-ответ на уровне роутера или глобального exception handler.
"""


class SyncBackError(Exception):
    """Базовый класс для всех доменных исключений."""


class DocumentNotFoundError(SyncBackError):
    """Документ не найден или не принадлежит проекту."""


class ProjectNotFoundError(SyncBackError):
    """Проект не найден или недоступен текущему пользователю."""


class SourceNotFoundError(SyncBackError):
    """Источник не найден или не принадлежит проекту."""


class FileTooLargeError(SyncBackError):
    """Загружаемый файл превышает допустимый размер."""


class UnsupportedFormatError(SyncBackError):
    """Формат файла не поддерживается парсером (например, .doc)."""


class AnalysisJobNotFoundError(SyncBackError):
    """Задание анализа не найдено."""


class ReviewVersionConflictError(SyncBackError):
    """P0-2: review_version в БД изменилась пока клиент редактировал документ.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """
