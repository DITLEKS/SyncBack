"""
Юнит-тесты на None-guard'ы в _process_source(): если job/document/source удалены между
постановкой Celery-подзадачи в очередь и её выполнением, подзадача должна вернуть
контролируемый {"status": "failed", "error_code": "..._NOT_FOUND"}, а не упасть с AttributeError.

Реальные Postgres/MinIO/LLM не поднимаем — репозитории и isolated_db_session подменены
фейками: выполнение не доходит до сети/БД благодаря ранним return в guard-проверках.
MinioStorage/ManualUploadConnector/get_llm_client вызывается до входа в isolated_db_session, но их
конструкторы не делают сетевых вызовов (minio.Minio() только сохраняет конфиг, а
LLM_PROVIDER=stub в .env даёт StubLLMClient без реального HTTP-клиента).
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.workers.tasks.analysis_tasks as analysis_tasks


@asynccontextmanager
async def _fake_session_cm():
    yield SimpleNamespace()


def _patch_session(monkeypatch):
    monkeypatch.setattr(analysis_tasks, "isolated_db_session", _fake_session_cm)


def _repo_returning(value):
    """Фабрика репозитория: конструктор принимает session и возвращает объект с
    get_by_id, всегда дающим заданное значение (None или фейк-запись)."""

    def _factory(_session):
        return SimpleNamespace(get_by_id=AsyncMock(return_value=value))

    return _factory


@pytest.mark.asyncio
async def test_process_source_returns_job_not_found_when_job_missing(monkeypatch):
    _patch_session(monkeypatch)
    monkeypatch.setattr(analysis_tasks, "AnalysisJobRepository", _repo_returning(None))

    result = await analysis_tasks._process_source(str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["status"] == "failed"
    assert result["error_code"] == "JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_process_source_returns_document_not_found_when_document_missing(monkeypatch):
    _patch_session(monkeypatch)
    fake_job = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4())
    monkeypatch.setattr(analysis_tasks, "AnalysisJobRepository", _repo_returning(fake_job))
    monkeypatch.setattr(analysis_tasks, "DocumentRepository", _repo_returning(None))

    result = await analysis_tasks._process_source(str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["status"] == "failed"
    assert result["error_code"] == "DOCUMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_process_source_returns_source_not_found_when_source_missing(monkeypatch):
    _patch_session(monkeypatch)
    fake_job = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4())
    fake_document = SimpleNamespace(id=uuid.uuid4(), storage_key="projects/p/documents/d/file.txt")
    monkeypatch.setattr(analysis_tasks, "AnalysisJobRepository", _repo_returning(fake_job))
    monkeypatch.setattr(analysis_tasks, "DocumentRepository", _repo_returning(fake_document))
    monkeypatch.setattr(analysis_tasks, "SourceRepository", _repo_returning(None))

    result = await analysis_tasks._process_source(str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["status"] == "failed"
    assert result["error_code"] == "SOURCE_NOT_FOUND"
