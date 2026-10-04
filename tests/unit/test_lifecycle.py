"""Жизненные циклы документа и задачи анализа: переходы и допустимые действия."""

from __future__ import annotations

import pytest

from app.domain.exceptions import InvalidDocumentStatusError
from app.domain.lifecycle import AnalysisJobLifecycle, DocumentLifecycle
from app.domain.value_objects import AnalysisJobStatusVO as Job
from app.domain.value_objects import DocumentStatusVO as Doc
from app.infrastructure.db.models.enums import DocumentStatus as OrmDocumentStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Doc.DRAFT, Doc.IN_PROGRESS),
        (Doc.DRAFT, Doc.DRAFT),
        (Doc.IN_PROGRESS, Doc.AWAITING_APPROVAL),
        (Doc.IN_PROGRESS, Doc.READY),
        (Doc.IN_PROGRESS, Doc.DRAFT),
        (Doc.AWAITING_APPROVAL, Doc.READY),
        (Doc.AWAITING_APPROVAL, Doc.DRAFT),
        (Doc.AWAITING_APPROVAL, Doc.AWAITING_APPROVAL),
        (Doc.READY, Doc.DRAFT),
        (Doc.READY, Doc.AWAITING_APPROVAL),
    ],
)
def test_document_allowed_transitions(current: Doc, target: Doc) -> None:
    assert DocumentLifecycle.transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Doc.DRAFT, Doc.READY),
        (Doc.DRAFT, Doc.AWAITING_APPROVAL),
        (Doc.IN_PROGRESS, Doc.IN_PROGRESS),
        (Doc.AWAITING_APPROVAL, Doc.IN_PROGRESS),
        (Doc.READY, Doc.IN_PROGRESS),
        (Doc.READY, Doc.READY),
    ],
)
def test_document_forbidden_transitions(current: Doc, target: Doc) -> None:
    with pytest.raises(InvalidDocumentStatusError):
        DocumentLifecycle.transition(current, target)


def test_document_transition_accepts_orm_enum_and_string() -> None:
    assert DocumentLifecycle.transition(OrmDocumentStatus.DRAFT, Doc.IN_PROGRESS) is Doc.IN_PROGRESS
    assert DocumentLifecycle.transition("ready", Doc.DRAFT) is Doc.DRAFT
    assert DocumentLifecycle.can_review(OrmDocumentStatus.AWAITING_APPROVAL)


def test_document_permissions_by_status() -> None:
    rules = {
        Doc.DRAFT: dict(analyze=True, sources=True, review=False, export=False, delete=True),
        Doc.IN_PROGRESS: dict(
            analyze=False, sources=False, review=False, export=False, delete=False
        ),
        Doc.AWAITING_APPROVAL: dict(
            analyze=True, sources=False, review=True, export=False, delete=True
        ),
        Doc.READY: dict(analyze=True, sources=True, review=False, export=True, delete=True),
    }
    for status, expected in rules.items():
        actual = dict(
            analyze=DocumentLifecycle.can_start_analysis(status),
            sources=DocumentLifecycle.can_edit_sources(status),
            review=DocumentLifecycle.can_review(status),
            export=DocumentLifecycle.can_export(status),
            delete=DocumentLifecycle.can_delete(status),
        )
        assert actual == expected, status


def test_analysis_confirmation_and_reset_sets() -> None:
    assert DocumentLifecycle.analysis_needs_confirmation(Doc.READY)
    assert not DocumentLifecycle.analysis_needs_confirmation(Doc.AWAITING_APPROVAL)
    assert DocumentLifecycle.can_reset_review(Doc.READY)
    assert not DocumentLifecycle.can_reset_review(Doc.DRAFT)
    assert DocumentLifecycle.has_review_results(Doc.READY)
    assert not DocumentLifecycle.has_review_results(Doc.IN_PROGRESS)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Job.PENDING, Job.DISPATCHED),
        (Job.PENDING, Job.FAILED),
        (Job.PENDING, Job.CANCELLED),
        (Job.DISPATCHED, Job.PROCESSING),
        (Job.DISPATCHED, Job.SUCCESS),
        (Job.DISPATCHED, Job.CANCELLED),
        (Job.PROCESSING, Job.SUCCESS),
        (Job.PROCESSING, Job.PARTIAL_SUCCESS),
        (Job.PROCESSING, Job.FAILED),
        (Job.PROCESSING, Job.CANCELLED),
    ],
)
def test_job_allowed_transitions(current: Job, target: Job) -> None:
    assert AnalysisJobLifecycle.transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Job.PENDING, Job.PROCESSING),
        (Job.PENDING, Job.SUCCESS),
        (Job.PROCESSING, Job.DISPATCHED),
        (Job.SUCCESS, Job.FAILED),
        (Job.FAILED, Job.FAILED),
        (Job.CANCELLED, Job.PROCESSING),
    ],
)
def test_job_forbidden_transitions(current: Job, target: Job) -> None:
    with pytest.raises(InvalidDocumentStatusError):
        AnalysisJobLifecycle.transition(current, target)


def test_job_active_and_terminal_sets_cover_all_statuses() -> None:
    assert AnalysisJobLifecycle.ACTIVE | AnalysisJobLifecycle.TERMINAL == set(Job)
    assert not AnalysisJobLifecycle.ACTIVE & AnalysisJobLifecycle.TERMINAL
    for status in AnalysisJobLifecycle.ACTIVE:
        assert AnalysisJobLifecycle.can_cancel(status)
    for status in AnalysisJobLifecycle.TERMINAL:
        assert not AnalysisJobLifecycle.can_cancel(status)
    assert AnalysisJobLifecycle.can_dispatch(Job.PENDING)
    assert not AnalysisJobLifecycle.can_dispatch(Job.DISPATCHED)


def test_document_status_follows_job_status() -> None:
    doc_for = AnalysisJobLifecycle.document_status_for
    assert doc_for(Job.PENDING) is Doc.DRAFT
    assert doc_for(Job.DISPATCHED) is Doc.IN_PROGRESS
    assert doc_for(Job.PROCESSING) is Doc.IN_PROGRESS
    assert doc_for(Job.SUCCESS, has_pending_suggestions=True) is Doc.AWAITING_APPROVAL
    assert doc_for(Job.SUCCESS, has_pending_suggestions=False) is Doc.READY
    assert doc_for(Job.PARTIAL_SUCCESS, has_pending_suggestions=True) is Doc.AWAITING_APPROVAL
    assert doc_for(Job.FAILED) is Doc.DRAFT
    assert doc_for(Job.CANCELLED) is Doc.DRAFT
