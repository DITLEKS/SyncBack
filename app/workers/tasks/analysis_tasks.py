"""
Celery-задачи пайплайна анализа. LLM вызывается отдельно на каждый источник —
ретрай/dead-letter логика работает по каждому источнику независимо.

Путь в репозитории: app/workers/tasks/analysis_tasks.py

ИСПРАВЛЕНО (предыдущие раунды):
1. SourceRef теперь строится с доменным SourceKind.
2. Гонка при параллельных analysis_jobs: обновляем current_analysis_job_id
   только если job новее уже сохранённого.
3. None-проверки job/document/source в _process_source.
4. _finalize_job: session.commit() в try/except с recovery-сессией.
5. _finalize_job: job.partial_success = True при частичном успехе.
6. _finalize_job: один проход по source_results.
7. Безопасный r.get('source_id', '?').
8. _is_cancelled() хелпер.
11. asyncio.run() напрямую без лишней _run() обёртки.
12. Проверка CANCELLED перед bulk_create.

PERF-PARSE (этот раунд): parse-once document artifact.
Проблема: _process_source скачивал и парсил документ заново для каждого
источника — O(S) обращений к MinIO + O(S) парсингов одного файла.

Решение:
- _start_job: после получения source_ids скачивает документ один раз,
  парсит, сохраняет plain_text в Redis с ключом doc_artifact:{job_id}
  и TTL = DOC_ARTIFACT_TTL_SECONDS (1800 сек).
- _load_doc_plain_text(job_id, document): читает plain_text из Redis;
  при промахе кеша (истёк TTL, воркер перезапустился) делает
  download+parse напрямую — graceful degradation, не ломает поведение.
- _process_source: вызывает _load_doc_plain_text вместо прямого download.
- _finalize_job: удаляет ключ из Redis после финализации (cleanup).
"""

import asyncio
import logging
import uuid

from celery import chord

from app.core.config import get_settings
from app.domain.exceptions import DocumentParseError, LLMInvalidResponseError, LLMTimeoutError
from app.domain.interfaces.source_connector import SourceKind, SourceRef
from app.infrastructure.cache.sync_redis_client import get_sync_redis_client
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository
from app.infrastructure.db.session import isolated_db_session
from app.infrastructure.llm.factory import get_llm_client
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.queue.dead_letter_store import DeadLetterStore
from app.infrastructure.source_connectors.manual_upload_connector import ManualUploadConnector
from app.infrastructure.storage.minio_storage import MinioStorage
from app.workers.celery_app import celery_app
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

logger = logging.getLogger("syncscribe.workers.analysis")

# Module-level singletons — создаются один раз при загрузке модуля.
_settings = get_settings()
_parser_registry = DocumentParserRegistry()
_storage = MinioStorage(_settings)
_connector = ManualUploadConnector(_storage, _parser_registry)
_llm_client = get_llm_client(_settings)

# PERF-PARSE: константы для Redis-артефакта parsed document.
DOC_ARTIFACT_KEY_PREFIX = "doc_artifact"
DOC_ARTIFACT_TTL_SECONDS = 1800  # 30 минут — достаточно для любого chord'а


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_cancelled(job) -> bool:
    """Проверка статуса CANCELLED — хелпер чтобы не дублировать условие."""
    return job.status == AnalysisJobStatus.CANCELLED


def _doc_artifact_key(job_id: str) -> str:
    """Redis-ключ для plain_text артефакта документа."""
    return f"{DOC_ARTIFACT_KEY_PREFIX}:{job_id}"


async def _load_doc_plain_text(job_id: str, document) -> str:
    """PERF-PARSE: возвращает plain_text документа.

    Сначала пробует Redis-кеш (заполняется в _start_job).
    При промахе — fallback на прямой download+parse из MinIO.
    Graceful degradation: если Redis недоступен, поведение деградирует
    к старому пути без ошибки.
    """
    redis_key = _doc_artifact_key(job_id)
    try:
        redis = get_sync_redis_client()
        cached = redis.get(redis_key)
        if cached is not None:
            return cached.decode("utf-8") if isinstance(cached, bytes) else cached
    except Exception:
        logger.warning(
            "Redis недоступен при чтении doc_artifact — fallback на MinIO",
            extra={"job_id": job_id, "document_id": str(document.id)},
        )

    # Fallback: download + parse напрямую.
    try:
        raw_bytes = await _storage.download(document.storage_key)
        parsed = _parser_registry.parse_by_filename(document.storage_key, raw_bytes)
        return parsed.plain_text
    except Exception as exc:
        raise DocumentParseError(
            f"Не удалось распарсить документ {document.id}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------

async def _start_job(job_id: str) -> list[str]:
    async with isolated_db_session() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("run_analysis_job вызван для несуществующего job_id", extra={"job_id": job_id})
            return []
        if job.status not in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING):
            return []
        if not await job_repo.mark_processing_if_active(job.id):
            return []
        await session.refresh(job)

        document = await document_repo.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ",
                extra={"job_id": job_id, "document_id": str(job.document_id)},
            )
            await job_repo.update_status(
                job, AnalysisJobStatus.FAILED, error_code="DOCUMENT_NOT_FOUND", error_message="Документ не найден"
            )
            return []
        if document.current_analysis_job_id == job.id:
            document.status = DocumentStatus.IN_PROGRESS
            await session.commit()

        await session.refresh(document, ["sources"])
        source_ids = [str(source.id) for source in document.sources]

        # PERF-PARSE: скачиваем и парсим документ один раз, кладём plain_text в Redis.
        # Подзадачи (chord) читают из кеша — O(1) vs O(S) download+parse.
        if source_ids:
            try:
                raw_bytes = await _storage.download(document.storage_key)
                parsed = _parser_registry.parse_by_filename(document.storage_key, raw_bytes)
                redis = get_sync_redis_client()
                redis.setex(
                    _doc_artifact_key(job_id),
                    DOC_ARTIFACT_TTL_SECONDS,
                    parsed.plain_text.encode("utf-8"),
                )
                logger.debug(
                    "doc_artifact сохранён в Redis",
                    extra={"job_id": job_id, "document_id": str(document.id), "text_len": len(parsed.plain_text)},
                )
            except Exception:
                # Не прерываем pipeline — подзадачи используют fallback.
                logger.warning(
                    "Не удалось сохранить doc_artifact в Redis — подзадачи используют fallback",
                    extra={"job_id": job_id, "document_id": str(document.id)},
                )

        return source_ids


async def _process_source(job_id: str, source_id: str) -> dict:
    async with isolated_db_session() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)
        source_repo = SourceRepository(session)
        suggestion_repo = SuggestionRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error(
                "process_source_for_analysis_job вызван для несуществующего job_id",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "JOB_NOT_FOUND", "error_message": "Задача анализа не найдена"}

        if _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        document = await document_repo.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ при обработке источника",
                extra={"job_id": job_id, "source_id": source_id, "document_id": str(job.document_id)},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "DOCUMENT_NOT_FOUND", "error_message": "Документ не найден"}

        source = await source_repo.get_by_id(uuid.UUID(source_id))
        if source is None:
            logger.error(
                "Источник удалён до обработки подзадачи анализа",
                extra={"job_id": job_id, "source_id": source_id},
            )
            return {"source_id": source_id, "status": "failed", "error_code": "SOURCE_NOT_FOUND", "error_message": "Источник не найден"}

        source_ref = SourceRef(
            id=source.id, name=source.name, type=SourceKind(source.type.value), storage_key=source.storage_key,
            text_content=source.text_content, url=source.url, uploaded_at=source.uploaded_at,
        )

        # PERF-PARSE: используем кешированный plain_text вместо повторного download+parse.
        plain_text = await _load_doc_plain_text(job_id, document)

        source_text = await _connector.fetch(source_ref)
        batch = await _llm_client.generate_suggestions(plain_text, source_text, document.format.value)

        suggestions = map_to_suggestions(batch, job.id, source_reference=source.name)

        # Проверяем CANCELLED до записи в БД.
        await session.refresh(job)
        if _is_cancelled(job):
            return {"source_id": source_id, "status": "cancelled"}

        await suggestion_repo.bulk_create(suggestions)

    return {"source_id": source_id, "status": "success", "suggestions_count": len(suggestions)}


async def _finalize_job(job_id: str, source_results: list[dict]) -> None:
    async with isolated_db_session() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)
        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("finalize_analysis_job вызван для несуществующего job_id", extra={"job_id": job_id})
            return
        if _is_cancelled(job):
            # Чистим артефакт даже при отмене.
            _cleanup_doc_artifact(job_id)
            return
        document = await document_repo.get_by_id(job.document_id)
        if document is None:
            await job_repo.update_status(job, AnalysisJobStatus.FAILED, "DOCUMENT_NOT_FOUND", "Документ не найден")
            _cleanup_doc_artifact(job_id)
            return

        # Один проход вместо трёх: два list comprehension + sum.
        succeeded: list[dict] = []
        failed: list[dict] = []
        suggestions_count = 0
        for r in source_results:
            if r.get("status") == "success":
                succeeded.append(r)
                suggestions_count += int(r.get("suggestions_count", 0))
            elif r.get("status") == "failed":
                failed.append(r)

        is_current = document.current_analysis_job_id == job.id

        if succeeded:
            message = None
            if failed:
                job.partial_success = True
                message = "Не обработаны источники: " + ", ".join(
                    f"{r.get('source_id', '?')} ({r.get('error_code')})" for r in failed
                )
            await job_repo.update_status(job, AnalysisJobStatus.SUCCESS, error_message=message)
            if is_current:
                document.status = (
                    DocumentStatus.AWAITING_APPROVAL if suggestions_count else DocumentStatus.READY
                )
        elif failed:
            message = "; ".join(f"{r.get('source_id', '?')}: {r.get('error_message')}" for r in failed)
            await job_repo.update_status(job, AnalysisJobStatus.FAILED, "ALL_SOURCES_FAILED", message)
            if is_current:
                document.status = DocumentStatus.DRAFT
        else:
            await job_repo.update_status(
                job,
                AnalysisJobStatus.FAILED,
                "NO_SOURCES_ATTACHED",
                "К документу не привязано ни одного источника",
            )
            if is_current:
                document.status = DocumentStatus.DRAFT

        try:
            await session.commit()
        except Exception as commit_exc:  # noqa: BLE001
            logger.exception(
                "Ошибка коммита при финализации job — откатываем статус документа в DRAFT",
                extra={"job_id": job_id, "document_id": str(job.document_id)},
            )
            await session.rollback()
            try:
                async with isolated_db_session() as recovery_session:
                    recovery_job_repo = AnalysisJobRepository(recovery_session)
                    recovery_doc_repo = DocumentRepository(recovery_session)
                    recovery_job = await recovery_job_repo.get_by_id(uuid.UUID(job_id))
                    if recovery_job is not None:
                        await recovery_job_repo.update_status(
                            recovery_job,
                            AnalysisJobStatus.FAILED,
                            error_code="COMMIT_ERROR",
                            error_message=f"Ошибка коммита финализации: {commit_exc}",
                        )
                    recovery_doc = await recovery_doc_repo.get_by_id(job.document_id)
                    if recovery_doc is not None and recovery_doc.status == DocumentStatus.IN_PROGRESS:
                        recovery_doc.status = DocumentStatus.DRAFT
                        await recovery_session.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось восстановить статус документа после ошибки коммита финализации",
                    extra={"job_id": job_id, "document_id": str(job.document_id)},
                )
        finally:
            # PERF-PARSE: удаляем Redis-артефакт после финализации job'а.
            _cleanup_doc_artifact(job_id)


def _cleanup_doc_artifact(job_id: str) -> None:
    """PERF-PARSE: удаляет Redis-ключ doc_artifact после завершения job.

    Не блокирует pipeline — ошибки логируются и проглатываются.
    TTL является страховкой: ключ удалится сам через DOC_ARTIFACT_TTL_SECONDS
    даже если cleanup упал.
    """
    try:
        redis = get_sync_redis_client()
        redis.delete(_doc_artifact_key(job_id))
    except Exception:
        logger.warning(
            "Не удалось удалить doc_artifact из Redis",
            extra={"job_id": job_id},
        )


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
