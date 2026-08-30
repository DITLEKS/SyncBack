"""
Celery-задачи пайплайна анализа. LLM вызывается отдельно на каждый источник —
ретрай/dead-letter логика работает по каждому источнику независимо.

Путь в репозитории: app/workers/tasks/analysis_tasks.py

ИСПРАВЛЕНО (этот раунд):
1. SourceRef теперь строится с доменным SourceKind (конвертация из
   infrastructure.SourceType.value), а не с инфраструктурным enum напрямую.
2. Гонка при параллельных analysis_jobs на одном документе: раньше
   document.current_analysis_job_id безусловно перезаписывался при финализации
   любого job'а — если более старый job завершался позже более нового (вполне
   возможно при разной длительности обработки разных источников), его результат
   мог затереть уже актуальные suggestions. Теперь обновляем "текущий" job только
   если он действительно новее (по created_at) уже сохранённого текущего job'а.
3. _process_source не проверял None для job/document/source после get_by_id, в отличие
   от уже защищённых _start_job/_finalize_job. Если источник/документ/job удаляли
   между постановкой в очередь и выполнением (или Celery повторно доставил уже
   неактуальную задачу после acks_late), подзадача падала с AttributeError
   вместо контролируемого "failed"-результата с понятным error_code. Теперь
   каждая из трёх сущностей проверяется отдельно, и подзадача возвращает
   {"status": "failed", "error_code": "...NOT_FOUND"} без retry (повторять нечего — запись
   уже не появится).
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
_parser_registry = DocumentParserRegistry()


def _run(coro):
    return asyncio.run(coro)


async def _start_job(job_id: str) -> list[str]:
    async with isolated_db_session() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("run_analysis_job вызван для несуществующего job_id", extra={"job_id": job_id})
            return []
        await job_repo.update_status(job, AnalysisJobStatus.PROCESSING)

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
        document.status = DocumentStatus.ANALYZING
        await session.commit()

        await session.refresh(document, ["sources"])
        return [str(source.id) for source in document.sources]


async def _process_source(job_id: str, source_id: str) -> dict:
    settings = get_settings()
    storage = MinioStorage(settings)
    connector = ManualUploadConnector(storage, _parser_registry)
    llm_client = get_llm_client(settings)

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

        try:
            raw_document_bytes = await storage.download(document.storage_key)
            parsed_document = _parser_registry.parse_by_filename(document.storage_key, raw_document_bytes)
        except Exception as exc:
            raise DocumentParseError(f"Не удалось распарсить документ {document.id}: {exc}") from exc

        source_text = await connector.fetch(source_ref)
        batch = await llm_client.generate_suggestions(parsed_document.plain_text, source_text, document.format.value)

        suggestions = map_to_suggestions(batch, job.id, source_reference=source.name)
        await suggestion_repo.bulk_create(suggestions)

    return {"source_id": source_id, "status": "success", "suggestions_count": len(suggestions)}


@celery_app.task(bind=True, acks_late=True)
def process_source_for_analysis_job(self, job_id: str, source_id: str) -> dict:
    settings = get_settings()
    try:
        return _run(_process_source(job_id, source_id))
    except (LLMTimeoutError, LLMInvalidResponseError, DocumentParseError) as exc:
        if self.request.retries < settings.llm_max_retries:
            backoff_seconds = settings.llm_timeout_seconds * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=backoff_seconds) from exc
        dead_letter_store = DeadLetterStore(get_sync_redis_client())
        dead_letter_store.push(job_id, source_id, error_code=type(exc).__name__, error_message=str(exc))
        logger.error(
            "Источник не обработан после исчерпания retries",
            extra={"job_id": job_id, "source_id": source_id, "error_type": type(exc).__name__, "retries": self.request.retries},
        )
        return {"source_id": source_id, "status": "failed", "error_code": type(exc).__name__, "error_message": str(exc)}


async def _finalize_job(job_id: str, source_results: list[dict]) -> None:
    async with isolated_db_session() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            logger.error("finalize_analysis_job вызван для несуществующего job_id", extra={"job_id": job_id})
            return
        document = await document_repo.get_by_id(job.document_id)
        if document is None:
            logger.error(
                "AnalysisJob ссылается на несуществующий документ при финализации",
                extra={"job_id": job_id, "document_id": str(job.document_id)},
            )
            await job_repo.update_status(
                job, AnalysisJobStatus.FAILED, error_code="DOCUMENT_NOT_FOUND", error_message="Документ не найден"
            )
            return

        succeeded = [r for r in source_results if r.get("status") == "success"]
        failed = [r for r in source_results if r.get("status") == "failed"]

        if succeeded:
            error_message = None
            if failed:
                error_message = "Не обработаны источники: " + ", ".join(f"{r['source_id']} ({r.get('error_code')})" for r in failed)
            await job_repo.update_status(job, AnalysisJobStatus.SUCCESS, error_message=error_message)
            document.status = DocumentStatus.ANALYZED
        elif failed:
            error_message = "; ".join(f"{r['source_id']}: {r.get('error_message')}" for r in failed)
            await job_repo.update_status(job, AnalysisJobStatus.FAILED, error_code="ALL_SOURCES_FAILED", error_message=error_message)
            document.status = DocumentStatus.ERROR
        else:
            await job_repo.update_status(job, AnalysisJobStatus.FAILED, error_code="NO_SOURCES_ATTACHED", error_message="К документу не привязано ни одного источника")
            document.status = DocumentStatus.ERROR

        current_job_id = document.current_analysis_job_id
        should_update_current = current_job_id is None or current_job_id == job.id
        if not should_update_current:
            current_job = await job_repo.get_by_id(current_job_id)
            should_update_current = current_job is None or job.created_at >= current_job.created_at
        if should_update_current:
            document.current_analysis_job_id = job.id

        await session.commit()


@celery_app.task(bind=True)
def finalize_analysis_job(self, source_results: list[dict], job_id: str) -> None:
    _run(_finalize_job(job_id, source_results))


@celery_app.task(bind=True)
def run_analysis_job(self, job_id: str) -> None:
    source_ids = _run(_start_job(job_id))
    if not source_ids:
        _run(_finalize_job(job_id, []))
        return
    header = [process_source_for_analysis_job.s(job_id, source_id) for source_id in source_ids]
    callback = finalize_analysis_job.s(job_id=job_id)
    chord(header)(callback)
