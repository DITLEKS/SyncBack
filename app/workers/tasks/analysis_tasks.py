"""
Celery-задачи пайплайна анализа.

Путь в репозитории: app/workers/tasks/analysis_tasks.py

РЕФАКТОРИНГ (этот раунд):
- _finalize_job D(21) → D(6): вынесены _tally_results(), _apply_job_outcome(),
  _recover_after_commit_failure().
- _process_source C(15) → C(7): I/O-пайплайн вынесен в _run_source_pipeline().
- P2: заменены ORM enum на domain VO, все репозитории вынесены через IUnitOfWork.
- Parse-once: document скачивается и парсится один раз в _start_job,
  plain_text кэшируется в Redis. _run_source_pipeline читает кэш и деградирует
  до прямого скачивания при cache miss (истёкший TTL, недоступный Redis).
"""

import asyncio
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

# Module-level singletons: создаются один раз при загрузке модуля.
_settings = get_settings()
_parser_registry = DocumentParserRegistry()
_storage = MinioStorage(_settings)
_connector = ManualUploadConnector(_storage, _parser_registry)
_llm_client = get_llm_client(_settings)

# Префикс Redis-ключа для кэшированного plain_text документа.
_PARSED_DOC_KEY_PREFIX = "parsed_doc:"


# ---------------------------------------------------------------------------
# Parse-once: кэширование plain_text документа
# ---------------------------------------------------------------------------

def _parsed_doc_key(job_id: str) -> str:
    return f"{_PARSED_DOC_KEY_PREFIX}{job_id}"


async def _cache_parsed_document(
    job_id: str,
    storage_key: str,
    document_format: str,
    sources_count: int,
) -> str:
    """Скачать и распарсить документ один раз, сохранить plain_text в Redis.

    TTL рассчитывается как max(llm_timeout * sources_count * 2, 300) секунд —
    достаточно, чтобы дожить до последнего источника в chord с учётом retry.

    Возвращает plain_text (нужен для первого источника без лишнего round-trip).
    """
    raw_bytes = await _storage.download(storage_key)
    parsed = _parser_registry.parse_by_filename(storage_key, raw_bytes)
    plain_text = parsed.plain_text

    ttl = max(_settings.llm_timeout_seconds * sources_count * 2, 300)
    redis = get_redis_client()
    await redis.setex(_parsed_doc_key(job_id), ttl, plain_text)

    return plain_text


async def _get_cached_plain_text(job_id: str) -> str | None:
    """Прочитать plain_text из Redis. Возвращает None при cache miss."""
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
    """Удалить ключ кэша после финализации job."""
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
    suggestions_count: int


def _tally_results(source_results: list[dict]) -> _Tally:
    """Один проход по результатам источников → счётчики."""
    succeeded: list[dict] = []
    failed: list[dict] = []
    suggestions_count = 0
    for r in source_results:
        status = r.get("status")
        if status == "success":
            succeeded.append(r)
            suggestions_count += int(r.get("suggestions_count", 0))
        elif status == "failed":
            failed.append(r)
    return _Tally(succeeded, failed, suggestions_count)


async def _apply_job_outcome(
    job,
    document,
    tally: _Tally,
    uow: IUnitOfWork,
) -> None:
    """Записать итоговый статус job и документа по итогам тальирования."""
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
            document.status = (
                DocumentStatusVO.AWAITING_APPROVAL
                if tally.suggestions_count
                else DocumentStatusVO.READY
            )
    elif tally.failed:
        message = "; ".join(
            f"{r.get('source_id', '?')}: {r.get('error_message')}" for r in tally.failed
        )
        await uow.jobs.update_status(
            job, AnalysisJobStatusVO.FAILED, "ALL_SOURCES_FAILED", message
        )
        if is_current:
            document.status = DocumentStatusVO.DRAFT
    else:
        await uow.jobs.update_status(
            job,
            AnalysisJobStatusVO.FAILED,
            "NO_SOURCES_ATTACHED",
            "К документу не привязано ни одного источника",
        )
        if is_current:
            document.status = DocumentStatusVO.DRAFT


async def _recover_after_commit_failure(
    job_id: str,
    document_id: uuid.UUID,
    commit_exc: Exception,
) -> None:
    """Компенсирующая транзакция: job → FAILED, документ → DRAFT."""
    try:
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
                recovery_doc.status = DocumentStatusVO.DRAFT
                await uow.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось восстановить статус документа после ошибки коммита финализации",
            extra={"job_id": job_id, "document_id": str(document_id)},
        )


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

async def _start_job(job_id: str) -> list[str]:
    """Подготовить job к выполнению и вернуть список source_id.

    Дополнительно кэширует plain_text документа в Redis (parse-once),
    чтобы каждый _process_source не скачивал и не парсил файл повторно.
    Если кэширование не удалось — продолжаем: каждый источник деградирует
    до прямого скачивания.
    """
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "run_analysis_job вызван для несуществующего job_id",
                extra={"job_id": job_id},
            )
            return []
        if job.status not in (
            AnalysisJobStatusVO.PENDING,
            AnalysisJobStatusVO.PROCESSING,
        ):
            return []
        if not await uow.jobs.mark_processing_if_active(job.id):
            return []
        await uow._session.refresh(job)

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ",
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
            document.status = DocumentStatusVO.IN_PROGRESS
            await uow.commit()

        await uow._session.refresh(document, ["sources"])
        source_ids = [str(source.id) for source in document.sources]

    # Parse-once: кэшируем plain_text вне DB-сессии.
    if source_ids and document.storage_key:
        try:
            await _cache_parsed_document(
                job_id=job_id,
                storage_key=document.storage_key,
                document_format=document.format.value,
                sources_count=len(source_ids),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось закэшировать plain_text документа; "
                "каждый источник будет скачивать файл отдельно",
                extra={"job_id": job_id, "document_id": str(document.id)},
            )

    return source_ids


async def _run_source_pipeline(
    job_id: uuid.UUID,
    document_storage_key: str,
    document_format: str,
    source_ref: SourceRef,
) -> list:
    """I/O-тяжёлая часть обработки источника: plain_text → LLM → map.

    Читает plain_text из Redis-кэша (parse-once). При cache miss (TTL истёк
    или Redis недоступен) — деградирует до прямого скачивания из MinIO.
    Это гарантирует, что ни один источник не упадёт из-за проблем с кэшем.
    """
    plain_text = await _get_cached_plain_text(str(job_id))

    if plain_text is None:
        logger.debug(
            "Cache miss для plain_text документа; скачиваем из MinIO",
            extra={"job_id": str(job_id), "storage_key": document_storage_key},
        )
        try:
            raw_bytes = await _storage.download(document_storage_key)
            parsed = _parser_registry.parse_by_filename(document_storage_key, raw_bytes)
            plain_text = parsed.plain_text
        except Exception as exc:
            raise DocumentParseError(
                f"Не удалось распарсить документ (storage_key={document_storage_key}): {exc}"
            ) from exc

    source_text = await _connector.fetch(source_ref)
    batch = await _llm_client.generate_suggestions(
        plain_text, source_text, document_format
    )
    return map_to_suggestions(batch, job_id, source_reference=source_ref.name)


async def _process_source(job_id: str, source_id: str) -> dict:
    """DB-оркестрация для одного источника.

    I/O-пайплайн вынесен в _run_source_pipeline().
    """
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "process_source_for_analysis_job вызван для несуществующего job_id",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "JOB_NOT_FOUND", "error_message": "Задача анализа не найдена"}

        if _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        document = await uow.documents.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ при обработке источника",
                extra={"job_id": job_id, "source_id": source_id, "document_id": str(job.document_id)},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "DOCUMENT_NOT_FOUND", "error_message": "Документ не найден"}

        source = await uow.sources.get_by_id(uuid.UUID(source_id))
        if source is None:
            logger.error(
                "Источник удалён до обработки подзадачи анализа",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "SOURCE_NOT_FOUND", "error_message": "Источник не найден"}

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

    # I/O-пайплайн выполняется вне сессии — не держим соединение в ожидании LLM.
    suggestions = await _run_source_pipeline(
        job_id=uuid.UUID(job_id),
        document_storage_key=document_storage_key,
        document_format=document_format,
        source_ref=source_ref,
    )

    async with isolated_uow() as uow:
        # Перепроверяем CANCELLED после долгого I/O.
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None or _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        await uow.suggestions.bulk_create(suggestions)
        await uow.commit()

    return {"source_id": source_id, "status": "success", "suggestions_count": len(suggestions)}


async def _finalize_job(job_id: str, source_results: list[dict]) -> None:
    """Финализировать job, обновить статус документа, очистить кэш."""
    async with isolated_uow() as uow:
        job = await uow.jobs.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "finalize_analysis_job вызван для несуществующего job_id",
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
        return asyncio.run(_process_source(job_id, source_id))
    except (LLMTimeoutError, LLMInvalidResponseError, DocumentParseError) as exc:
        if self.request.retries < _settings.llm_max_retries:
            backoff_seconds = _settings.llm_timeout_seconds * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=backoff_seconds) from exc
        dead_letter_store = DeadLetterStore(get_sync_redis_client())
        dead_letter_store.push(job_id, source_id, error_code=type(exc).__name__, error_message=str(exc))
        logger.error(
            "Источник не обработан после исчерпания retries",
            extra={"job_id": job_id, "source_id": source_id, "error_type": type(exc).__name__, "retries": self.request.retries},
        )
        return {"source_id": source_id, "status": "failed", "error_code": type(exc).__name__, "error_message": str(exc)}


@celery_app.task(bind=True)
def finalize_analysis_job(self, source_results: list[dict], job_id: str) -> None:
    asyncio.run(_finalize_job(job_id, source_results))


@celery_app.task(bind=True)
def run_analysis_job(self, job_id: str) -> None:
    source_ids = asyncio.run(_start_job(job_id))
    if not source_ids:
        asyncio.run(_finalize_job(job_id, []))
        return
    header = [process_source_for_analysis_job.s(job_id, source_id) for source_id in source_ids]
    callback = finalize_analysis_job.s(job_id=job_id)
    chord(header)(callback)
