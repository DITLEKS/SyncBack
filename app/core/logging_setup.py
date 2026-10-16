"""Структурированное логирование с correlation id и защитой чувствительных данных."""

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

from app.core.config import get_settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)

_REDACTED = "***REDACTED***"
_SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "database_url",
    "dsn",
    "password",
    "private_key",
    "secret",
    "token",
}
_STANDARD_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"asctime", "message"}

# Строковые сообщения и traceback могут содержать DSN или заголовки, даже если
# вызывающий код не использовал structured extra. Значения заменяются до вывода.
_DSN_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/@\s]*)(?::([^@\s]*))?@")
_AUTH_RE = re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_COOKIE_RE = re.compile(r"(?i)\b((?:set-)?cookie\s*[:=]\s*)[^\r\n]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b[a-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|token|authorization|"
    r"(?:set[_-]?)?cookie|dsn|database[_-]?url|credentials?|private[_-]?key)"
    r"[a-z0-9_.-]*\b\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    compact = normalized.replace("_", "")
    for part in _SENSITIVE_KEY_PARTS:
        if part in normalized or part.replace("_", "") in compact:
            return True
    return False


def _redact_text(value: str) -> str:
    value = _DSN_RE.sub(lambda match: f"{match.group(1)}***:***@", value)
    value = _AUTH_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    value = _BEARER_RE.sub(lambda match: f"{match.group(1)} {_REDACTED}", value)
    value = _COOKIE_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{_REDACTED}", value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if isinstance(key, str) and _is_sensitive_key(key) else _redact(val)
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class CorrelationJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }

        request_id = request_id_var.get()
        task_id = task_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if task_id:
            payload["task_id"] = task_id

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_LOG_RECORD_KEYS or key in payload:
                continue
            payload[key] = _REDACTED if _is_sensitive_key(key) else _redact(value)

        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainCorrelationFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get() or "-"
        task_id = task_id_var.get() or "-"
        message = _redact_text(record.getMessage())
        base = (
            f"{self.formatTime(record)} [{record.levelname}] req={request_id} "
            f"task={task_id} {record.name}: {message}"
        )
        if record.exc_info:
            base += "\n" + _redact_text(self.formatException(record.exc_info))
        return base


def configure_logging() -> None:
    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter = (
        CorrelationJsonFormatter() if settings.log_format == "json" else PlainCorrelationFormatter()
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    for noisy_logger in ("uvicorn", "uvicorn.access", "uvicorn.error", "celery"):
        logging.getLogger(noisy_logger).handlers.clear()
        logging.getLogger(noisy_logger).propagate = True
