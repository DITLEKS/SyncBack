<<<<<<< HEAD
"""Доменные исключения SyncBack.

Каждое исключение соответствует одной бизнес-ситуации и конвертируется
в HTTP-ответ на уровне роутера или глобального exception handler.
"""
=======
"""Expected application and domain errors."""
>>>>>>> origin/fix/high-priority-review-findings


class SyncBackError(Exception):
    """Base class for expected failures handled by the API layer."""


<<<<<<< HEAD
# ---------------------------------------------------------------------------
# Document / Project / Source
# ---------------------------------------------------------------------------


class DocumentNotFoundError(SyncBackError):
    """Документ не найден или не принадлежит проекту."""
=======
DomainError = SyncBackError
>>>>>>> origin/fix/high-priority-review-findings


class DocumentNotFoundError(DomainError):
    pass


class ProjectNotFoundError(DomainError):
    pass


class SourceNotFoundError(DomainError):
    pass


class FileTooLargeError(DomainError):
    pass


<<<<<<< HEAD
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
    """review_version в БД изменилась, пока клиент редактировал документ.

    Сигнализирует роутеру вернуть HTTP 409 Conflict.
    """
=======
class UnsupportedFormatError(DomainError):
    pass


UnsupportedFileFormatError = UnsupportedFormatError
>>>>>>> origin/fix/high-priority-review-findings


# Алиас для обратной совместимости — suggestions router импортирует это имя.
StaleReviewVersionError = ReviewVersionConflictError


<<<<<<< HEAD
class OptimisticLockError(SyncBackError):
    """Версия ревью на клиенте устарела — документ был изменён параллельным запросом.
=======
class AnalysisJobNotFoundError(DomainError):
    pass


class AnalysisAlreadyRunningError(DomainError):
    pass


class AnalysisJobNotCancellableError(DomainError):
    pass


class SuggestionNotFoundError(DomainError):
    pass


class SuggestionAlreadyDecidedError(DomainError):
    pass


class ReviewNotCompleteError(DomainError):
    pass


class ReviewVersionConflictError(DomainError):
    pass
>>>>>>> origin/fix/high-priority-review-findings


StaleReviewVersionError = ReviewVersionConflictError
OptimisticLockError = ReviewVersionConflictError


<<<<<<< HEAD
class SourceLockError(SyncBackError):
    """Изменение источников запрещено, пока документ находится
    в статусе IN_PROGRESS или AWAITING_APPROVAL.
    """
=======
class SourceLockError(DomainError):
    pass


class InvalidCredentialsError(DomainError):
    pass


class InvalidTokenError(DomainError):
    pass


class EmailAlreadyRegisteredError(DomainError):
    pass


class AccountTemporarilyLockedError(DomainError):
    pass


class DocumentParseError(DomainError):
    pass


class LLMTimeoutError(DomainError):
    pass


class LLMInvalidResponseError(DomainError):
    pass
>>>>>>> origin/fix/high-priority-review-findings
