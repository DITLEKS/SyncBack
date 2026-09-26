"""Expected application and domain errors."""


class SyncBackError(Exception):
    """Base class for expected failures handled by the API layer."""


DomainError = SyncBackError


class DocumentNotFoundError(DomainError):
    pass


class ProjectNotFoundError(DomainError):
    pass


class SourceNotFoundError(DomainError):
    pass


class FileTooLargeError(DomainError):
    pass


class UnsupportedFormatError(DomainError):
    pass


UnsupportedFileFormatError = UnsupportedFormatError


class InvalidDocumentStatusError(DomainError):
    pass


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


StaleReviewVersionError = ReviewVersionConflictError
OptimisticLockError = ReviewVersionConflictError


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
