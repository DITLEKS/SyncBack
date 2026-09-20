class DomainError(Exception):
    """Базовый класс для всех доменных ошибок SyncScribe."""


class EmailAlreadyRegisteredError(DomainError):
    pass


class InvalidCredentialsError(DomainError):
    pass


class AccountTemporarilyLockedError(DomainError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Аккаунт временно заблокирован, повтор через {retry_after_seconds} сек.")


class InvalidTokenError(DomainError):
    pass


class UserNotFoundError(DomainError):
    pass


class ProjectNotFoundError(DomainError):
    pass


class ProjectAccessDeniedError(DomainError):
    pass


class DocumentNotFoundError(DomainError):
    pass


class SourceNotFoundError(DomainError):
    pass


class SuggestionNotFoundError(DomainError):
    pass


class SuggestionAlreadyDecidedError(DomainError):
    """Выбрасывается при гонке двойного accept/reject одной правки."""


class UnsupportedFileFormatError(DomainError):
    pass


class FileTooLargeError(DomainError):
    pass


class AnalysisJobNotFoundError(DomainError):
    pass


class DocumentParseError(DomainError):
    """Файл битый или не парсится."""


class LLMTimeoutError(DomainError):
    pass


class LLMInvalidResponseError(DomainError):
    pass


class AnalysisAlreadyRunningError(DomainError):
    pass


class InvalidDocumentStatusError(DomainError):
    pass


class AnalysisJobNotCancellableError(DomainError):
    pass


class ReviewNotCompleteError(DomainError):
    pass


class UnsupportedFormatError(DomainError):
    pass


# P0-2: оптимистическая блокировка review
class StaleReviewVersionError(DomainError):
    """PUT /review: клиент прислал устаревший review_version (If-Match не совпал).

    HTTP-слой должен вернуть 412 Precondition Failed.
    """
