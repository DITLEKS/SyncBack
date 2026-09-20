"""Доменные исключения SyncBack.

Каждое исключение соответствует одной бизнес-ситуации и конвертируется
в HTTP-ответ на уровне роутера или глобального exception handler.

ИСПРАВЛЕНО (review):
- Удалены дублирующие объявления классов (InvalidDocumentStatusError,
  AnalysisJobNotCancellableError, ReviewNotCompleteError).
- Удалены объявления через несуществующий DomainError.
- Добавлен StaleReviewVersionError как алиас ReviewVersionConflictError
  для обратной совместимости с импортами в suggestion_service.py.
- Добавлен SourceLockError и OptimisticLockError в единственном экземпляре.
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


class SourceLockError(SyncBackError):
    """Изменение источников запрещено, пока документ находится
    в статусе IN_PROGRESS или AWAITING_APPROVAL.
    """


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


# Алиас для обратной совместимости — suggestion_service импортирует оба имени.
StaleReviewVersionError = ReviewVersionConflictError


class OptimisticLockError(SyncBackError):
    """Версия ревью на клиенте устарела — документ был изменён параллельным запросом.

    Клиент должен перезагрузить состояние (GET /editor) и повторить сохранение.
    """
