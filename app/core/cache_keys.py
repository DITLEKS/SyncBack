"""
Префиксы и хелперы ключей Redis в одном месте, чтобы не размножать строки-ключи.

Импортируйте этот модуль везде, где требуется сформировать Redis-ключ.
"""

from __future__ import annotations


class CacheKeys:
    """Namespace для префиксов Redis-ключей."""

    #: Префикс кэша разобранного plain_text документа (parse-once пайплайн).
    PARSED_DOC_PREFIX: str = "parsed_doc:"

    @classmethod
    def parsed_doc(cls, job_id: str) -> str:
        """Redis-ключ для plain_text конкретного job_id."""
        return f"{cls.PARSED_DOC_PREFIX}{job_id}"
