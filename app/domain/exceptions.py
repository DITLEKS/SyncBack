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


class UnsupportedFileFormatError(DomainError):
    pass


class FileTooLargeError(DomainError):
    pass


class AnalysisJobNotFoundError(DomainError):
    pass


class DocumentParseError(DomainError):
    """Файл битый или не парсится — статус документа/job переводится в error с этим кодом."""


class LLMTimeoutError(DomainError):
    pass


class LLMInvalidResponseError(DomainError):
    pass
