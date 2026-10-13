"""Контракт PUT /review: edited_text допустим только для принятых решений и не пуст."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.api.schemas.review import ReviewSaveRequest, SuggestionDecision


def test_edited_text_allowed_for_accepted():
    decision = SuggestionDecision(
        suggestion_id=uuid.uuid4(), decision="accepted", edited_text="новый текст"
    )
    assert decision.edited_text == "новый текст"


def test_edited_text_rejected_decision_is_invalid():
    with pytest.raises(ValidationError):
        SuggestionDecision(suggestion_id=uuid.uuid4(), decision="rejected", edited_text="x")


def test_edited_text_empty_string_is_invalid():
    with pytest.raises(ValidationError):
        SuggestionDecision(suggestion_id=uuid.uuid4(), decision="accepted", edited_text="")


def test_edited_text_defaults_to_none_in_request():
    request = ReviewSaveRequest(
        review_version=0,
        decisions=[{"suggestion_id": str(uuid.uuid4()), "decision": "accepted"}],
    )
    assert request.decisions[0].edited_text is None
