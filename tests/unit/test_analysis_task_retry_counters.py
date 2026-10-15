"""P0-2b: busy и таймаут LLM используют независимые счётчики повторов Celery-задачи."""

from __future__ import annotations

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry

from app.workers.tasks import analysis_tasks

task = analysis_tasks.process_source_for_analysis_job
JOB_ID = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def process_result(monkeypatch: pytest.MonkeyPatch):
    """Подменяет _run_async: задача получает заранее заданный результат _process_source."""

    def install(result: dict) -> None:
        def _run_async(coro):
            coro.close()
            return result

        monkeypatch.setattr(analysis_tasks, "_run_async", _run_async)

    return install


@pytest.fixture
def retry_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def _retry(**options):
        calls.append(options)
        raise Retry("retry")

    monkeypatch.setattr(task, "retry", _retry)
    return calls


def _busy() -> dict:
    return {"source_id": SOURCE_ID, "status": "busy"}


def _retryable() -> dict:
    return {
        "source_id": SOURCE_ID,
        "status": "retryable",
        "error_code": "LLM_UNAVAILABLE",
        "error_message": "timeout",
    }


def test_busy_increments_only_busy_counter(process_result, retry_calls) -> None:
    process_result(_busy())

    with pytest.raises(Retry):
        task(JOB_ID, SOURCE_ID, busy_attempts=4, llm_attempts=2)

    assert retry_calls == [
        {
            "args": (),
            "kwargs": {
                "job_id": JOB_ID,
                "source_id": SOURCE_ID,
                "busy_attempts": 5,
                "llm_attempts": 2,
            },
            "countdown": analysis_tasks._BUSY_RETRY_COUNTDOWN_SECONDS,
        }
    ]


def test_busy_limit_is_independent_of_llm_attempts(process_result, retry_calls) -> None:
    process_result(_busy())

    result = task(JOB_ID, SOURCE_ID, busy_attempts=analysis_tasks._BUSY_RETRY_LIMIT)

    assert retry_calls == []
    assert result["status"] == "failed"
    assert result["error_code"] == "SOURCE_BUSY"


def test_llm_retry_keeps_busy_counter_and_backs_off(process_result, retry_calls) -> None:
    process_result(_retryable())

    with pytest.raises(Retry):
        task(JOB_ID, SOURCE_ID, busy_attempts=7, llm_attempts=1)

    assert retry_calls == [
        {
            "args": (),
            "kwargs": {
                "job_id": JOB_ID,
                "source_id": SOURCE_ID,
                "busy_attempts": 7,
                "llm_attempts": 2,
            },
            "countdown": analysis_tasks._LLM_RETRY_BACKOFF_SECONDS * 2,
        }
    ]


def test_llm_limit_is_not_consumed_by_busy_waits(process_result, retry_calls) -> None:
    process_result(_retryable())

    # Долгое ожидание занятого источника не отнимает бюджет повторов LLM.
    with pytest.raises(Retry):
        task(JOB_ID, SOURCE_ID, busy_attempts=analysis_tasks._BUSY_RETRY_LIMIT, llm_attempts=0)
    assert retry_calls[-1]["kwargs"]["llm_attempts"] == 1

    result = task(JOB_ID, SOURCE_ID, busy_attempts=0, llm_attempts=analysis_tasks._LLM_RETRY_LIMIT)
    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_UNAVAILABLE"
    assert result["error_message"] == "timeout"


def test_max_retries_exceeded_becomes_failed_result(process_result, monkeypatch) -> None:
    process_result(_busy())

    def _retry(**options):
        raise MaxRetriesExceededError("limit")

    monkeypatch.setattr(task, "retry", _retry)
    result = task(JOB_ID, SOURCE_ID)

    assert result["status"] == "failed"
    assert result["error_code"] == "SOURCE_BUSY"


def test_ok_result_is_returned_as_is(process_result, retry_calls) -> None:
    ok = {"source_id": SOURCE_ID, "status": "ok", "count": 3}
    process_result(ok)

    assert task(JOB_ID, SOURCE_ID) == ok
    assert retry_calls == []


def test_lease_exceeds_hard_time_limit() -> None:
    assert analysis_tasks._SOURCE_LEASE_SECONDS > analysis_tasks._TASK_TIME_LIMIT_SECONDS
    assert task.time_limit == analysis_tasks._TASK_TIME_LIMIT_SECONDS
    assert task.soft_time_limit == analysis_tasks._TASK_SOFT_TIME_LIMIT_SECONDS
    assert analysis_tasks.finalize_analysis_job.time_limit is None
