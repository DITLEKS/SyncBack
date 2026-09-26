"""
Celery-задачи пайплайна анализа.

Путь в репозитории: app/workers/tasks/analysis_tasks.py

ИСПРАВЛЕНИЯ (этот раунд):
- M-NEW-1: asyncio.run() заменён на _run_async(), который безопасно запускает
  корутину в существующем event loop или создаёт новый если петля нет.
  Теперь работает и с prefork (чистый asyncio), и с gevent/eventlet.
- M-NEW-2: добавлена ветка "cancelled" в _tally_results() / _apply_job_outcome():
  если все результаты cancelled — job переходит в CANCELLED, не в FAILED.
- M-NEW-4: исправлена опечатка "соурцес" → "sources" в docstring IUnitOfWork.refresh.
- Все ранее внесённые исправления (парсe-once, H-4, M-1/M-2, L-3, L-5, H-7, M-8, M-10) сохранены.
"""

import asyncio
import functools
import logging
import uuid
from typing import TYPE_CHECKING, NamedTuple

from celery import chord

from app.core.config import get_settings
from app.domain.exceptions import DocumentParseError, LLMInvalidResponseError, LLMTimeoutError
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
from app.infrastructure.storage.minio_storage import MinioStorage
from app.workers.celery_app import celery_app
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

logger = logging.getLogger("syncscribe.workers.analysis")

_PARSED_DOC_KEY_PREFIX = "parsed_doc:"


# ---------------------------------------------------------------------------
# M-NEW-1: Безопасный запуск async из синхронного Celery-таска
# ---------------------------------------------------------------------------

def _run_async(coro):
    """M-NEW-1: запустить корутину безопасно в любом пуле Celery.

    asyncio.run() падает с RuntimeError если event loop уже запущен
    (gevent/eventlet с monkey-patching). Эта функция:
    1. Пытается получить текущий loop.
    2. Если loop есть и он запущен — используем loop.run_until_complete().
    3. Если loop есть, но закрыт — запускаем напрямую.
    4. Если loop нет — создаём новый через asyncio.run().
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # gevent/eventlet с monkey-patch: loop запущен потоком.
        # run_until_complete в этом случае заблокирует — используем nest_asyncio-совместимый патрон.
        import concurrent.futures  # noqa: PLC0415
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    elif loop is not None and not loop.is_closed():
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# M-8: Cached provider-функции
# ---------------------------------------------------------------------------

@functools.cache
def _get_parser_registry() -> DocumentParserRegistry:
    return DocumentParserRegistry()


@functools.cache
def _get_storage() -> MinioStorage:
    return MinioStorage(get_settings())


@functools.cache
def _get_connector() -> ManualUploadConnector:
    return ManualUploadConnector(_get_storage(), _get_parser_registry())


@functools.cache
def _get_llm_client():
    return get_llm_client(get_settings())


# ---------------------------------------------------------------------------
# Parse-once
# ---------------------------------------------------------------------------

def _parsed_doc_key(job_id: str) -> str:
    return f"{_PARSED_DOC_KEY_PREFIX}{job_id}"


async def _cache_parsed_document(
    job_id: str,
    storage_key: str,
    document_format: str,
    sources_count: int,
) -> str:
    settings = get_settings()
    raw_bytes = await _get_storage().download(storage_key)
    parsed = _get_parser_registry().parse_by_filename(storage_key, raw_bytes)
    plain_text = parsed.plain_text
    ttl = max(settings.llm_timeout_seconds * sources_count * 2, 300)
    redis = get_redis_client()
    await redis.setex(_parsed_doc_key(job_id), ttl, plain_text)
    return plain_text


async def _get_cached_plain_text(job_id: str) -> str | None:
    try:
        redis = get_redis_client()
        return await redis.get(_parsed_doc_key(job_id))
    except Exception:  # noqa: BLE001
        logger.warning(
            "Redis недоступен при чтении кэша документа",
            extra={"job_id": job_id},
        )
        return None


async def _cleanup_parsed_cache(job_id: str) -> None:
    try:
        redis = get_redis_client()
        await redis.delete(_parsed_doc_key(job_id))
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось удалить кэш документа", extra={"job_id": job_id})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_cancelled(job) -> bool:
    return job.status == AnalysisJobStatusVO.CANCELLED


class _Tally(NamedTuple):
    succeeded: list[dict]
    failed: list[dict]
    cancelled: list[dict]
    suggestions_count: int


def _tally_results(source_results: list[dict]) -> _Tally:
    """M-NEW-2: один проход по результатам → счётчики, включая ветку cancelled."""
    succeeded: list[dict] = []
    failed: list[dict] = []
    cancelled: list[dict] = []
    suggestions_count = 0
    for r in source_results:
        status = r.get("status")
        if status == "success":
            succeeded.append(r)
            suggestions_count += int(r.get("suggestions_count", 0))
        elif status == "failed":
            failed.append(r)
        elif status == "cancelled":
            cancelled.append(r)
    return _Tally(succeeded, failed, cancelled, suggestions_count)


async def _apply_job_outcome(
    job,
    document,
    tally: _Tally,
    uow: IUnitOfWork,
) -> None:
    """M-NEW-2: записать итоговый статус job и документа.

    Логика приоритетов статуса:
    1. Если есть хотя бы один succeeded — SUCCESS (с partial_success если есть failed).
    2. Если все cancelled (и нет succeeded/failed) — CANCELLED.
    3. Если есть failed, но нет succeeded — ALL_SOURCES_FAILED.
    4. Пустой список — NO_SOURCES_ATTACHED.
    """
    is_current = document.current_analysis_job_id == job.id

    if tally.succeeded:
        message: str | None = None
        if tally.failed:
            job.partial_success = True
            message = "Не обработаны источники: " + ", ".join(
                f"{r.get('source_id', '?')} ({r.get('error_code')})" for r in tally.failed
            )
        await uow.jobs.update_status(job, AnalysisJobStatusVO.SUCCESS, error_message=message)
        if is_current:
            new_doc_status = (
                DocumentStatusVO.AWAITING_APPROVAL
                if tally.suggestions_count
                else DocumentStatusVO.READY
            )
            await uow.documents.update_status(document, new_doc_status)

    elif tally.cancelled and not tally.failed:
        # M-NEW-2: все источники были отменены — job переходит в CANCELLED, не FAILED.
        await uow.jobs.update_status(
            job,
            AnalysisJobStatusVO.CANCELLED,
            error_message="Задача была отменена пользователем",
        )
        if is_current:
            await uow.documents.update_status(document, DocumentStatusVO.DRAFT)

    elif tally.failed:
        message = "; ".join(
            f"{r.get('source_id', '?')}: {r.get('error_message')}" for r in tally.failed
        )
        await uow.jobs.update_status(
            job, AnalysisJobStatusVO.FAILED, "ALL_SOURCES_FAILED", message
        )
        if is_current:
            await uow.documents.update_status(document, DocumentStatusVO.DRAFT)

    else:
        # Пустой tally: нет источников вообще.
        await uow.jobs.update_status(
            job,
            AnalysisJobStatusVO.FAILED,
            "NO_SOURCES_ATTACHED",
            "К документу не привязано ни одного источника",
        )
        if is_current:
            await uow.documents.update_status(document, DocumentStatusVO.DRAFT)


async def _recover_after_commit_failure(
    job_id: str,
    document_id: uuid.UUID,
    commit_exc: Exception,
) -> None:
    """H-7: recovery-транзакция с пробрасыванием __cause__."""
    async with isolated_uow() as uow:
        recovery_job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if recovery_job is not None:
            await uow.jobs.update_status(
                recovery_job,
                AnalysisJobStatusVO.FAILED,
                error_code="COMMIT_ERROR",
                error_message=f"Ошибка коммита финализации: {commit_exc}",
            )

        recovery_doc = await uow.documents.get_by_id(document_id)
        if (
            recovery_doc is not None
            and recovery_doc.status == DocumentStatusVO.IN_PROGRESS
        ):
            await uow.documents.update_status(recovery_doc, DocumentStatusVO.DRAFT)

        try:
            await uow.commit()
        except Exception as recovery_exc:  # noqa: BLE001
            logger.exception(
                "Recovery-коммит тоже упал; документ может остаться в IN_PROGRESS",
                extra={"job_id": job_id, "document_id": str(document_id)},
            )
            raise recovery_exc from commit_exc


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

async def _start_job(job_id: str) -> list[str]:
    """M-NEW-4: каждая транзакция живёт в своём isolated_uow()-блоке."""
    document_id: uuid.UUID | None = None
    storage_key: str | None = None
    document_format: str | None = None

    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "run_analysis_job вызван для несуществующего job_id (JOB_NOT_FOUND)",
                extra={"job_id": job_id},
            )
            return []
        if job.status not in (
            AnalysisJobStatusVO.PENDING,
            AnalysisJobStatusVO.PROCESSING,
        ):
            logger.info(
                "_start_job: job уже в финальном статусе, пропускаем",
                extra={"job_id": job_id, "status": job.status.value},
            )
            return []
        if not await uow.jobs.mark_processing_if_active(job.id):
            logger.info(
                "_start_job: не удалось занять job (race), пропускаем",
                extra={"job_id": job_id},
            )
            return []
        await uow.refresh(job)

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ (DOCUMENT_NOT_FOUND)",
                extra={"job_id": job_id, "document_id": str(job.document_id)},
            )
            await uow.jobs.update_status(
                job,
                AnalysisJobStatusVO.FAILED,
                error_code="DOCUMENT_NOT_FOUND",
                error_message="Документ не найден",
            )
            await uow.commit()
            return []

        if document.current_analysis_job_id == job.id:
            await uow.documents.update_status(document, DocumentStatusVO.IN_PROGRESS)

        document_id = document.id
        storage_key = document.storage_key
        document_format = document.format.value

        await uow.commit()

    source_ids: list[str] = []
    async with isolated_uow() as uow:
        document = await uow.documents.get_by_id(document_id)
        if document is None:
            return []
        await uow.refresh(document, ["sources"])
        source_ids = [str(source.id) for source in document.sources]

    if source_ids and storage_key:
        try:
            await _cache_parsed_document(
                job_id=job_id,
                storage_key=storage_key,
                document_format=document_format,
                sources_count=len(source_ids),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось закэшировать plain_text документа; "
                "каждый источник будет скачивать файл отдельно",
                extra={"job_id": job_id, "document_id": str(document_id)},
            )

    return source_ids


async def _run_source_pipeline(
    job_id: uuid.UUID,
    document_storage_key: str,
    document_format: str,
    source_ref: SourceRef,
) -> list:
    plain_text = await _get_cached_plain_text(str(job_id))

    if plain_text is None:
        logger.debug(
            "Cache miss для plain_text документа; скачиваем из MinIO",
            extra={"job_id": str(job_id), "storage_key": document_storage_key},
        )
        try:
            raw_bytes = await _get_storage().download(document_storage_key)
            parsed = _get_parser_registry().parse_by_filename(document_storage_key, raw_bytes)
            plain_text = parsed.plain_text
        except Exception as exc:
            raise DocumentParseError(
                f"Не удалось распарсить документ (storage_key={document_storage_key}): {exc}"
            ) from exc

    source_text = await _get_connector().fetch(source_ref)
    batch = await _get_llm_client().generate_suggestions(
        plain_text, source_text, document_format
    )
    return map_to_suggestions(batch, job_id, source_reference=source_ref.name)


async def _process_source(job_id: str, source_id: str) -> dict:
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "process_source_for_analysis_job: job не найден (JOB_NOT_FOUND)",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "JOB_NOT_FOUND",
                    "error_message": "Задача анализа не найдена"}

        if _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ (DOCUMENT_NOT_FOUND)",
                extra={"job_id": job_id, "source_id": source_id,
                        "document_id": str(job.document_id)},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "DOCUMENT_NOT_FOUND",
                    "error_message": "Документ не найден"}

        source = await uow.sources.get_by_id(uuid.UUID(source_id))
        if source is None:
            logger.error(
                "Источник удалён до обработки подзадачи анализа",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "SOURCE_NOT_FOUND",
                    "error_message": "Источник не найден"}

        source_ref = SourceRef(
            id=source.id,
            name=source.name,
            type=SourceKind(source.type.value),
            storage_key=source.storage_key,
            text_content=source.text_content,
            url=source.url,
            uploaded_at=source.uploaded_at,
        )
        document_storage_key = document.storage_key
        document_format = document.format.value

    suggestions = await _run_source_pipeline(
        job_id=uuid.UUID(job_id),
        document_storage_key=document_storage_key,
        document_format=document_format,
        source_ref=source_ref,
    )

    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None or _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        await uow.suggestions.bulk_create(suggestions)
        await uow.commit()

    return {"source_id": source_id, "status": "success", "suggestions_count": len(suggestions)}


async def _finalize_job(job_id: str, source_results: list[dict]) -> None:
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "finalize_analysis_job: job не найден (JOB_NOT_FOUND)",
                extra={"job_id": job_id},
            )
            return
        if _is_cancelled(job):
            await _cleanup_parsed_cache(job_id)
            return

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            await uow.jobs.update_status(
                job,
                AnalysisJobStatusVO.FAILED,
                "DOCUMENT_NOT_FOUND",
                "Документ не найден",
            )
            await uow.commit()
            await _cleanup_parsed_cache(job_id)
            return

        tally = _tally_results(source_results)
        await _apply_job_outcome(job, document, tally, uow)

        try:
            await uow.commit()
        except Exception as commit_exc:  # noqa: BLE001
            logger.exception(
                "Ошибка коммита при финализации job",
                extra={"job_id": job_id, "document_id": str(job.document_id)},
            )
            await uow.rollback()
            await _recover_after_commit_failure(job_id, job.document_id, commit_exc)
        finally:
            await _cleanup_parsed_cache(job_id)


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, acks_late=True)
def process_source_for_analysis_job(self, job_id: str, source_id: str) -> dict:
    try:
        return _run_async(_process_source(job_id, source_id))
    except (LLMTimeoutError, LLMInvalidResponseError, DocumentParseError) as exc:
        settings = get_settings()
        if self.request.retries < settings.llm_max_retries:
            backoff_seconds = settings.llm_timeout_seconds * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=backoff_seconds) from exc
        dead_letter_store = DeadLetterStore(get_sync_redis_client())
        dead_letter_store.push(job_id, source_id, error_code=type(exc).__name__,
                               error_message=str(exc))
        logger.error(
            "Источник не обработан после исчерпания retries",
            extra={"job_id": job_id, "source_id": source_id,
                   "error_type": type(exc).__name__, "retries": self.request.retries},
        )
        return {"source_id": source_id, "status": "failed",
                "error_code": type(exc).__name__, "error_message": str(exc)}


@celery_app.task(bind=True)
def finalize_analysis_job(self, source_results: list[dict], job_id: str) -> None:
    _run_async(_finalize_job(job_id, source_results))


@celery_app.task(bind=True)
def _chord_error_handler(self, request, exc, traceback, job_id: str = "") -> None:  # noqa: ARG002
    """L-5: on_error хэндлер chord."""
    if not job_id:
        logger.critical(
            "_chord_error_handler вызван без job_id — финализация невозможна, "
            "документ может зависнуть в IN_PROGRESS. Требуется ручное вмешательство.",
            extra={"exc": str(exc)},
        )
        return
    logger.error(
        "chord завершился с ошибкой; запускаем аварийную финализацию job",
        extra={"job_id": job_id, "exc": str(exc)},
    )
    _run_async(_finalize_job(job_id, []))


@celery_app.task(bind=True)
def run_analysis_job(self, job_id: str) -> None:
    source_ids = _run_async(_start_job(job_id))
    if not source_ids:
        _run_async(_finalize_job(job_id, []))
        return
    header = [process_source_for_analysis_job.s(job_id, source_id) for source_id in source_ids]
    callback = finalize_analysis_job.s(job_id=job_id)
    chord(header)(callback).on_error(_chord_error_handler.s(job_id=job_id))
