"""Celery-задачи пайплайна анализа документа.

Chord: process_source_for_analysis_job для каждого источника → finalize_analysis_job
с результатами всех источников. Каждая задача выполняется через asyncio.run()
(воркер должен работать с пулом prefork, см. celery_app.py), поэтому все
подключения (БД, Redis) создаются внутри запуска и закрываются при выходе.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings
from app.domain.exceptions import (
    DocumentParseError,
    LLMInputTooLargeError,
    LLMInvalidResponseError,
    LLMTimeoutError,
)
from app.domain.interfaces.event_publisher import IEventPublisher
from app.domain.interfaces.source_connector import SourceKind, SourceRef
from app.domain.lifecycle import AnalysisJobLifecycle, DocumentLifecycle
from app.domain.services.document_events import DocumentEventOutbox
from app.domain.value_objects import AnalysisJobStatusVO, SuggestionStatusVO
from app.infrastructure.db.session import isolated_uow
from app.infrastructure.events.publishers import RedisEventPublisher
from app.infrastructure.llm.factory import get_llm_client
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.source_connectors.manual_upload_connector import ManualUploadConnector
from app.infrastructure.source_connectors.url_connector import UrlConnector
from app.infrastructure.storage.minio_storage import MinioStorage
from app.workers.celery_app import celery_app
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

logger = logging.getLogger("syncscribe.workers.analysis")

_PARSED_DOC_KEY_PREFIX = "parsed_doc:"
_PARSED_DOC_TTL_SECONDS = 3600

# Точка подмены в тестах: фабрика UoW над изолированным подключением.
uow_factory = isolated_uow


def _run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Инфраструктура воркера (per-process)
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
    if source_kind == SourceKind.FILE:
        return _get_manual_connector()
    if source_kind == SourceKind.URL:
        return _get_url_connector()
    raise ValueError(f"Неизвестный SourceKind: {source_kind}")


@functools.cache
def _get_llm_client():
    return get_llm_client()


def _event_publisher() -> IEventPublisher:
    settings = get_settings()
    return RedisEventPublisher(settings.redis_url, settings.redis_sse_channel)


@asynccontextmanager
async def _redis() -> AsyncIterator[Redis]:
    """Клиент Redis на время одного asyncio.run: общий клиент нельзя переиспользовать
    между event loop, которые создаёт каждая задача."""
    client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def _build_source_ref(source) -> SourceRef:
    kind_map = {"file": SourceKind.FILE, "url": SourceKind.URL}
    source_kind = kind_map.get(source.type.value)
    if source_kind is None:
        raise ValueError(f"Неизвестный тип источника в БД: {source.type.value}")
    return SourceRef(
        id=source.id,
        name=source.name,
        type=source_kind,
        storage_key=source.storage_key,
        url=source.url,
        uploaded_at=source.created_at,
    )


# ---------------------------------------------------------------------------
# Кэш распарсенного текста документа (общий для всех источников одного job)
# ---------------------------------------------------------------------------


async def _get_cached_plain_text(job_id: uuid.UUID) -> str | None:
    try:
        async with _redis() as redis:
            return await redis.get(f"{_PARSED_DOC_KEY_PREFIX}{job_id}")
    except Exception:
        logger.warning("Redis недоступен, документ будет распарсен заново", exc_info=True)
        return None


async def _cache_plain_text(job_id: uuid.UUID, plain_text: str) -> None:
    try:
        async with _redis() as redis:
            await redis.set(
                f"{_PARSED_DOC_KEY_PREFIX}{job_id}", plain_text, ex=_PARSED_DOC_TTL_SECONDS
            )
    except Exception:
        logger.warning("Redis недоступен, текст документа не закэширован", exc_info=True)


async def _load_document_text(job_id: uuid.UUID, storage_key: str) -> str:
    plain_text = await _get_cached_plain_text(job_id)
    if plain_text is not None:
        return plain_text
    try:
        raw_bytes = await _get_storage().download(storage_key)
        plain_text = _get_parser_registry().parse_by_filename(storage_key, raw_bytes).plain_text
    except Exception as exc:
        raise DocumentParseError(
            f"Не удалось распарсить документ (storage_key={storage_key}): {exc}"
        ) from exc
    await _cache_plain_text(job_id, plain_text)
    return plain_text


# ---------------------------------------------------------------------------
# Обработка одного источника
# ---------------------------------------------------------------------------


_GENERATION_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (LLMInputTooLargeError, "LLM_INPUT_TOO_LARGE"),
    (LLMTimeoutError, "LLM_UNAVAILABLE"),
    (LLMInvalidResponseError, "LLM_INVALID_RESPONSE"),
    (DocumentParseError, "DOCUMENT_PARSE_ERROR"),
)


def _generation_error_code(exc: Exception) -> str:
    for error_type, code in _GENERATION_ERROR_CODES:
        if isinstance(exc, error_type):
            return code
    return "GENERATION_ERROR"


def _failed(source_id: str, error_code: str, error_message: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "status": "failed",
        "error_code": error_code,
        "error_message": error_message,
    }


async def _process_source(job_id: str, source_id: str) -> dict[str, Any]:
    job_uuid = uuid.UUID(job_id)
    async with uow_factory() as uow:
        job = await uow.jobs.get_by_id(job_uuid)
        if job is None:
            logger.error("process_source: job не найден", extra={"job_id": job_id})
            return _failed(source_id, "JOB_NOT_FOUND", "Задача анализа не найдена")
        # Первый источник переводит задачу из очереди в обработку; если задачу
        # уже завершили (например, отменили), работать не над чем.
        if not await uow.jobs.mark_processing_if_active(job_uuid):
            return {"source_id": source_id, "status": "cancelled"}
        await uow.commit()

        source = await uow.sources.get_by_id(uuid.UUID(source_id))
        if source is None:
            return _failed(source_id, "SOURCE_NOT_FOUND", "Источник не найден")
        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            return _failed(source_id, "DOCUMENT_NOT_FOUND", "Документ не найден")

        source_ref = _build_source_ref(source)
        document_id = document.id
        storage_key = document.storage_key
        document_format = document.format.value

    try:
        plain_text = await _load_document_text(job_uuid, storage_key)
        source_text = await _get_connector_for(source_ref.type).fetch(source_ref)
        batch = await _get_llm_client().generate_suggestions(
            plain_text, source_text, document_format
        )
    except Exception as exc:
        logger.exception(
            "Ошибка генерации правок для источника",
            extra={"job_id": job_id, "source_id": source_id},
        )
        return _failed(source_id, _generation_error_code(exc), str(exc))

    suggestions = map_to_suggestions(batch, job_uuid, document_id)

    async with uow_factory() as uow:
        job = await uow.jobs.get_by_id(job_uuid)
        # Пока шла генерация, задачу могли отменить — тогда правки не сохраняем.
        if job is None or not AnalysisJobLifecycle.is_active(job.status):
            return {"source_id": source_id, "status": "cancelled"}
        await uow.suggestions.bulk_create(suggestions)
        await uow.commit()

    return {"source_id": source_id, "status": "ok", "count": len(suggestions)}


# ---------------------------------------------------------------------------
# Финализация
# ---------------------------------------------------------------------------


async def _finalize(results: list[dict[str, Any]], job_id: str) -> None:
    failed = [r for r in results if r.get("status") == "failed"]
    cancelled = [r for r in results if r.get("status") == "cancelled"]
    succeeded = [r for r in results if r.get("status") == "ok"]

    async with uow_factory() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("finalize: job не найден", extra={"job_id": job_id})
            return
        if AnalysisJobLifecycle.is_terminal(job.status):
            # Задачу уже завершили (например, отменили через API) — результат воркера не важнее.
            logger.info(
                "finalize: задача уже завершена",
                extra={"job_id": job_id, "status": job.status.value},
            )
            return

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error("finalize: документ не найден", extra={"job_id": job_id})
            return

        error_code: str | None = None
        error_message: str | None = None
        if cancelled:
            job_status = AnalysisJobStatusVO.CANCELLED
            error_code, error_message = "ANALYSIS_CANCELLED", "Анализ отменён"
        elif failed and not succeeded:
            job_status = AnalysisJobStatusVO.FAILED
            error_code = failed[0].get("error_code") or "GENERATION_ERROR"
            error_message = _join_errors(failed)
        elif failed:
            job_status = AnalysisJobStatusVO.PARTIAL_SUCCESS
            error_code, error_message = "PARTIAL_FAILURE", _join_errors(failed)
        else:
            job_status = AnalysisJobStatusVO.SUCCESS

        pending = await uow.suggestions.count_by_analysis_job_and_status(
            job.id, SuggestionStatusVO.PENDING
        )
        job = await uow.jobs.update_status(
            job, AnalysisJobLifecycle.transition(job.status, job_status), error_code, error_message
        )
        job.partial_success = job_status is AnalysisJobStatusVO.PARTIAL_SUCCESS
        outbox = DocumentEventOutbox(_event_publisher())
        if document.current_analysis_job_id == job.id:
            doc_status = AnalysisJobLifecycle.document_status_for(
                job_status, has_pending_suggestions=pending > 0
            )
            document = await uow.documents.update_status(
                document, DocumentLifecycle.transition(document.status, doc_status)
            )
            outbox.record(document)
        await uow.commit()
        await outbox.flush(uow)


def _join_errors(failed: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{r.get('source_id')}: {r.get('error_message') or r.get('error_code')}" for r in failed
    )[:2000]


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="process_source_for_analysis_job", bind=True, max_retries=3)
def process_source_for_analysis_job(self, job_id: str, source_id: str) -> dict[str, Any]:
    return _run_async(_process_source(job_id, source_id))


@celery_app.task(name="finalize_analysis_job", bind=True)
def finalize_analysis_job(self, results: list[dict[str, Any]], job_id: str) -> None:
    _run_async(_finalize(results, job_id))
