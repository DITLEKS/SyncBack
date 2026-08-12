"""
Celery-задачи пайплайна анализа. LLM вызывается отдельно на каждый источник —
retry/dead-letter логика работает по каждому источнику независимо: сбой по одному не
блокирует остальные и не валит весь job. run_analysis_job запускает group через chord
и агрегирует финальный статус в finalize_analysis_job.
"""

import asyncio
import logging
import uuid

from celery import chord
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.exceptions import DocumentParseError, LLMInvalidResponseError, LLMTimeoutError
from app.domain.interfaces.source_connector import SourceRef
from app.infrastructure.cache.sync_redis_client import get_sync_redis_client
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.source_repository import SourceRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository
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


class _TaskDbSession:
    """
    Создаёт отдельный AsyncEngine + сессию строго на время одного Celery-вызова и
    гарантированно закрывает движок при выходе — движок никогда не покидает event loop,
    в котором был создан, поэтому проблема "attached to a different loop" структурно
    невозможна: у каждого вызова свой изолированный движок.
    """

    def __init__(self):
        settings = get_settings()
        self._engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=settings.debug)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def __aenter__(self) -> AsyncSession:
        self._session = self._sessionmaker()
        return await self._session.__aenter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._session.__aexit__(exc_type, exc, tb)
        await self._engine.dispose()


async def _start_job(job_id: str) -> list[str]:
    async with _TaskDbSession() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        await job_repo.update_status(job, AnalysisJobStatus.PROCESSING)

        document = await document_repo.get_by_id(job.document_id)
        document.status = DocumentStatus.ANALYZING
        await session.commit()

        await session.refresh(document, ["sources"])
        return [str(source.id) for source in document.sources]


async def _process_source(job_id: str, source_id: str) -> dict:
    settings = get_settings()
    storage = MinioStorage(settings)
    connector = ManualUploadConnector(storage, _parser_registry)
    llm_client = get_llm_client(settings)

    async with _TaskDbSession() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)
        source_repo = SourceRepository(session)
        suggestion_repo = SuggestionRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        document = await document_repo.get_by_id(job.document_id)
        source = await source_repo.get_by_id(uuid.UUID(source_id))

        source_ref = SourceRef(
            id=source.id, name=source.name, type=source.type, storage_key=source.storage_key,
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
            raise self.retry(exc=exc, countdown=backoff_seconds)
        dead_letter_store = DeadLetterStore(get_sync_redis_client())
        dead_letter_store.push(job_id, source_id, error_code=type(exc).__name__, error_message=str(exc))
        logger.error(
            "Источник не обработан после исчерпания retries",
            extra={"job_id": job_id, "source_id": source_id, "error_type": type(exc).__name__, "retries": self.request.retries},
        )
        return {"source_id": source_id, "status": "failed", "error_code": type(exc).__name__, "error_message": str(exc)}


async def _finalize_job(job_id: str, source_results: list[dict]) -> None:
    async with _TaskDbSession() as session:
        job_repo = AnalysisJobRepository(session)
        document_repo = DocumentRepository(session)

        job = await job_repo.get_by_id(uuid.UUID(job_id))
        document = await document_repo.get_by_id(job.document_id)

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