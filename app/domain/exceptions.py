"""Доменные исключения SyncBack.

Каждое исключение соответствует одной бизнес-ситуации и конвертируется
в HTTP-ответ на уровне роутера или глобального exception handler.

fix/review-critical-p0:
- Добавлены все исключения, которые импортируются сервисами, но отсутствовали.
- StaleReviewVersionError = алиас ReviewVersionConflictError (обратная совместимость).
"""


class SyncBackError(Exception):
    """Базовый класс для всех доменных исключений."""


# ---------------------------------------------------------------------------
# Document / Project / Source
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Document status transitions
# ---------------------------------------------------------------------------

class InvalidDocumentStatusError(SyncBackError):
    """Операция недопустима для текущего статуса документа.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


# ---------------------------------------------------------------------------
# Analysis jobs
# ---------------------------------------------------------------------------

class AnalysisJobNotFoundError(SyncBackError):
    """Задание анализа не найдено."""


class AnalysisAlreadyRunningError(SyncBackError):
    """Для документа уже выполняется анализ — нельзя запустить повторно.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class AnalysisJobNotCancellableError(SyncBackError):
    """Задание анализа нельзя отменить (уже завершено или отменено).

    Сигнализирует роутеру вернуть HTTP 422 Unprocessable Entity.
    """


# ---------------------------------------------------------------------------
# Suggestions / Review
# ---------------------------------------------------------------------------

class SuggestionNotFoundError(SyncBackError):
    """Правка не найдена или не принадлежит текущему документу."""


class SuggestionAlreadyDecidedError(SyncBackError):
    """Правка уже была принята/отклонена другим запросом (race condition).

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class ReviewNotCompleteError(SyncBackError):
    """Review нельзя завершить: не все правки рассмотрены или экспорт не удался.

    Сигнализирует роутеру вернуть HTTP 422 Unprocessable Entity.
    """


class ReviewVersionConflictError(SyncBackError):
    """P0-2: review_version в БД изменилась пока клиент редактировал документ.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class InvalidDocumentStatusError(DomainError):
    pass


class AnalysisJobNotCancellableError(DomainError):
    pass


class ReviewNotCompleteError(DomainError):
    pass


class OptimisticLockError(DomainError):
    """P0-2: версия ревью на клиенте устарела — документ был изменён параллельным запросом.

    Клиент должен перезагрузить состояние (GET /editor) и повторить сохранение.
    """


class SourceLockError(DomainError):
    """P0-6: изменение источников запрещено, пока документ находится
    в статусе IN_PROGRESS или AWAITING_APPROVAL.
    """
