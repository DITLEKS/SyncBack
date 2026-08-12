"""
Структурированное JSON-логирование с correlation id (request_id/task_id) через contextvars.
"""

import contextvars
import json
import logging
import sys
import time
from typing import Any

from app.core.config import get_settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)

_SENSITIVE_KEYS = {"password", "password_hash", "token", "authorization", "jwt_secret", "api_key"}


class CorrelationJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        task_id = task_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if task_id:
            payload["task_id"] = task_id

        for key, value in record.__dict__.items():
            if key in _SENSITIVE_KEYS or key.startswith("_") or key in logging.LogRecord.__dict__:
                continue
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainCorrelationFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get() or "-"
        task_id = task_id_var.get() or "-"
        base = f"{self.formatTime(record)} [{record.levelname}] req={request_id} task={task_id} {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter
    if settings.log_format == "json":
        formatter = CorrelationJsonFormatter()
    else:
        formatter = PlainCorrelationFormatter()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    for noisy_logger in ("uvicorn", "uvicorn.access", "uvicorn.error", "celery"):
        logging.getLogger(noisy_logger).handlers.clear()
        logging.getLogger(noisy_logger).propagate = True
