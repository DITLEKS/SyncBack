"""Доменные исключения SyncBack.

Каждое исключение соответствует одной бизнес-ситуации и конвертируется
в HTTP-ответ на уровне роутера или глобального exception handler.
"""


class SyncBackError(Exception):
    """Base class for expected failures handled by the API layer."""


# Алиас — всё доменное наследуется от DomainError
DomainError = SyncBackError


# ---------------------------------------------------------------------------
# Document / Project / Source
# ---------------------------------------------------------------------------


class DocumentNotFoundError(DomainError):
    """Документ не найден или не принадлежит проекту."""


class ProjectNotFoundError(DomainError):
    pass


class SourceNotFoundError(DomainError):
    pass


class FileTooLargeError(DomainError):
    pass


class UnsupportedFormatError(DomainError):
    pass


UnsupportedFileFormatError = UnsupportedFormatError


# ---------------------------------------------------------------------------
# Document status transitions
# ---------------------------------------------------------------------------


class InvalidDocumentStatusError(DomainError):
    """Операция недопустима для текущего статуса документа.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class DocumentParseError(DomainError):
    pass


# ---------------------------------------------------------------------------
# Analysis jobs
# ---------------------------------------------------------------------------


class AnalysisJobNotFoundError(DomainError):
    """Задание анализа не найдено."""


class JobNotFoundError(AnalysisJobNotFoundError):
    """job_id передан в воркер или сервис, но запись в БД отсутствует.

    Отличие от AnalysisJobNotFoundError: используется в контекстах,
    где job обязан существовать (например, внутри Celery-задачи после
    dispatch). Позволяет отличить «не существует» от «уже завершён».
    """


class AnalysisAlreadyRunningError(DomainError):
    """Для документа уже выполняется анализ — нельзя запустить повторно.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class AnalysisJobNotCancellableError(DomainError):
    """Задание анализа нельзя отменить (уже завершено или отменено).

    Сигнализирует роутеру вернуть HTTP 422 Unprocessable Entity.
    """


# ---------------------------------------------------------------------------
# Suggestions / Review
# ---------------------------------------------------------------------------


class SuggestionNotFoundError(DomainError):
    """Правка не найдена в БД вообще."""


class StaleSuggestionJobError(SuggestionNotFoundError):
    """Правка существует, но принадлежит устаревшему analysis job.

    M-9: явно разграничивает «не найдена» vs «не та версия анализа».
    Клиент должен перезагрузить список правок — документ был
    переанализирован с тех пор, как клиент получил suggestion_id.
    Возвращает HTTP 404 (наследует SuggestionNotFoundError).
    """


class SuggestionAlreadyDecidedError(DomainError):
    """Правка уже была принята/отклонена другим запросом (race condition).

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


class ReviewNotCompleteError(DomainError):
    """Review нельзя завершить: не все правки рассмотрены или экспорт не удался.

    Сигнализирует роутеру вернуть HTTP 422 Unprocessable Entity.
    """


class ReviewVersionConflictError(DomainError):
    """review_version в БД изменилась, пока клиент редактировал документ.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """


# Алиасы для обратной совместимости
StaleReviewVersionError = ReviewVersionConflictError
OptimisticLockError = ReviewVersionConflictError


# ---------------------------------------------------------------------------
# Auth / Credentials
# ---------------------------------------------------------------------------


class InvalidCredentialsError(DomainError):
    pass


class InvalidTokenError(DomainError):
    pass


class EmailAlreadyRegisteredError(DomainError):
    pass


class AccountTemporarilyLockedError(DomainError):
    pass


# ---------------------------------------------------------------------------
# Sources / Infrastructure
# ---------------------------------------------------------------------------


class SourceLockError(DomainError):
    """Изменение источников запрещено, пока документ находится
    в статусе IN_PROGRESS или AWAITING_APPROVAL.
    """


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class LLMTimeoutError(DomainError):
    pass


class LLMInvalidResponseError(DomainError):
    pass
