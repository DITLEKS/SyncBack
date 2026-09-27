"""
Celery-задачи пайплайна анализа документа.

P2: _build_source_ref больше не передаёт text_content (поле удалено из SourceRef).
    Все источники читаются через storage_key (FILE) или url (URL).

P3: _get_connector() заменена на _get_connector_for(source_kind) —
    роутинг FILE → ManualUploadConnector, URL → UrlConnector.
    Импорты ManualUploadConnector и UrlConnector ленивые (functools.cache).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from typing import Any

from app.domain.exceptions import (
    DocumentParseError,
    SourceNotFoundError,
)
from app.domain.interfaces.source_connector import SourceKind, SourceRef
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO
from app.infrastructure.cache.redis_client import get_redis_client
from app.infrastructure.cache.sync_redis_client import get_sync_redis_client
from app.infrastructure.db.session import isolated_uow
from app.infrastructure.llm.factory import get_llm_client
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.queue.dead_letter_store import DeadLetterStore
from app.infrastructure.source_connectors.manual_upload_connector import ManualUploadConnector
from app.infrastructure.source_connectors.url_connector import UrlConnector
from app.infrastructure.storage.minio_storage import MinioStorage
from app.workers.celery_app import celery_app
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

logger = logging.getLogger("syncscribe.workers.analysis")

_PARSED_DOC_KEY_PREFIX = "parsed_doc:"


# ---------------------------------------------------------------------------
# Безопасный запуск async из синхронного Celery-таска
# ---------------------------------------------------------------------------

def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        elif loop.is_closed():
            return asyncio.run(coro)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Синглтоны инфраструктуры (per-process)
# ---------------------------------------------------------------------------

@functools.cache
def _get_storage() -> MinioStorage:
    return MinioStorage()


@functools.cache
def _get_parser_registry() -> DocumentParserRegistry:
    return DocumentParserRegistry()


@functools.cache
def _get_manual_connector() -> ManualUploadConnector:
    return ManualUploadConnector(_get_storage(), _get_parser_registry())


@functools.cache
def _get_url_connector() -> UrlConnector:
    return UrlConnector()


def _get_connector_for(source_kind: SourceKind):
    """Роутинг: FILE → ManualUploadConnector, URL → UrlConnector."""
    if source_kind == SourceKind.FILE:
        return _get_manual_connector()
    if source_kind == SourceKind.URL:
        return _get_url_connector()
    raise ValueError(f"Неизвестный SourceKind: {source_kind}")


@functools.cache
def _get_llm_client():
    return get_llm_client()


# ---------------------------------------------------------------------------
# Построение SourceRef из ORM-модели
# ---------------------------------------------------------------------------

def _build_source_ref(source) -> SourceRef:
    """Конвертация ORM Source → domain SourceRef.

    P2: text_content убрано из SourceRef — NOTE больше не существует как отдельный тип.
    Бывшие NOTE-источники хранятся в MinIO и приходят с source_type=FILE.
    """
    kind_map = {
        "file": SourceKind.FILE,
        "url":  SourceKind.URL,
    }
    source_kind = kind_map.get(source.source_type.value)
    if source_kind is None:
        raise ValueError(f"Неизвестный source_type в БД: {source.source_type.value}")

    return SourceRef(
        id=source.id,
        name=source.name,
        type=source_kind,
        storage_key=source.storage_key,
        url=source.url,
        uploaded_at=source.uploaded_at,
    )


# ---------------------------------------------------------------------------
# Вспомогательные async-функции пайплайна
# ---------------------------------------------------------------------------

async def _get_cached_plain_text(job_id: str) -> str | None:
    try:
        redis = await get_redis_client()
        return await redis.get(f"{_PARSED_DOC_KEY_PREFIX}{job_id}")
    except Exception:
        return None


async def _cache_plain_text(job_id: str, plain_text: str, ttl: int = 3600) -> None:
    try:
        redis = await get_redis_client()
        await redis.set(f"{_PARSED_DOC_KEY_PREFIX}{job_id}", plain_text, ex=ttl)
    except Exception:
        pass


async def _is_cancelled_async(job_id: str) -> bool:
    try:
        redis = await get_redis_client()
        return await redis.exists(f"cancel:{job_id}") > 0
    except Exception:
        return False


def _is_cancelled(job) -> bool:
    return job.status == AnalysisJobStatusVO.CANCELLED


async def _generate_suggestions_for_source(
    job_id: uuid.UUID,
    document_storage_key: str,
    document_format: str,
    source_ref: SourceRef,
) -> list:
    plain_text = await _get_cached_plain_text(str(job_id))

    if plain_text is None:
        try:
            raw_bytes = await _get_storage().download(document_storage_key)
            parsed = _get_parser_registry().parse_by_filename(document_storage_key, raw_bytes)
            plain_text = parsed.plain_text
        except Exception as exc:
            raise DocumentParseError(
                f"Не удалось распарсить документ (storage_key={document_storage_key}): {exc}"
            ) from exc

    connector = _get_connector_for(source_ref.type)
    source_text = await connector.fetch(source_ref)
    batch = await _get_llm_client().generate_suggestions(
        plain_text, source_text, document_format
    )
    return map_to_suggestions(batch, job_id, source_reference=source_ref.name)


async def _process_source(job_id: str, source_id: str) -> dict:
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "process_source: job не найден",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed",
                    "error_code": "JOB_NOT_FOUND", "error_message": "Задача анализа не найдена"}

        if _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        source = await uow.sources.get_by_id(uuid.UUID(source_id))
        if source is None:
            return {"source_id": source_id, "status": "failed",
                    "error_code": "SOURCE_NOT_FOUND", "error_message": "Источник не найден"}

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            return {"source_id": source_id, "status": "failed",
                    "error_code": "DOCUMENT_NOT_FOUND", "error_message": "Документ не найден"}

        source_ref = _build_source_ref(source)

    try:
        suggestions = await _generate_suggestions_for_source(
            job_id=uuid.UUID(job_id),
            document_storage_key=document.storage_key,
            document_format=document.format.value,
            source_ref=source_ref,
        )
    except Exception as exc:
        logger.exception(
            "Ошибка генерации suggestions для источника",
            extra={"job_id": job_id, "source_id": source_id, "exc": str(exc)},
        )
        return {"source_id": source_id, "status": "failed",
                "error_code": "GENERATION_ERROR", "error_message": str(exc)}

    async with isolated_uow() as uow:
        for s in suggestions:
            uow.suggestions.add(s)
        await uow.commit()

    return {"source_id": source_id, "status": "ok", "count": len(suggestions)}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@celery_app.task(name="process_source_for_analysis_job", bind=True, max_retries=3)
def process_source_for_analysis_job(self, job_id: str, source_id: str) -> dict:
    return _run_async(_process_source(job_id, source_id))


@celery_app.task(name="finalize_analysis_job", bind=True)
def finalize_analysis_job(self, results: list[dict], job_id: str) -> None:
    _run_async(_finalize(results, job_id))


async def _finalize(results: list[dict], job_id: str) -> None:
    failed = [r for r in results if r.get("status") == "failed"]
    cancelled = [r for r in results if r.get("status") == "cancelled"]

    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("finalize: job не найден", extra={"job_id": job_id})
            return

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error("finalize: document не найден", extra={"job_id": job_id})
            return

        if cancelled:
            job.status = AnalysisJobStatusVO.CANCELLED
            document.status = DocumentStatusVO.DRAFT
        elif failed:
            job.status = AnalysisJobStatusVO.FAILED
            document.status = DocumentStatusVO.FAILED
        else:
            job.status = AnalysisJobStatusVO.COMPLETED
            document.status = DocumentStatusVO.AWAITING_APPROVAL

        await uow.commit()
