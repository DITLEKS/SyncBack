"""P1: в логах отброса правок нет текста документа и ответа LLM."""

import logging
import uuid
from types import SimpleNamespace

from app.workers.pipeline.suggestion_mapper import map_to_suggestions

SECRET_OLD_TEXT = "Конфиденциальный фрагмент документа 12345"
SECRET_RAW = "RAW-LLM-RESPONSE-SECRET"


def test_rejected_items_are_logged_without_user_text(
    caplog: "logging.LogCaptureFixture",
) -> None:
    item = SimpleNamespace(
        change_type="bogus",
        old_text=SECRET_OLD_TEXT,
        new_text="x",
        section_ref="Секция с персональными данными",
        rationale=None,
        insert_after=None,
        confidence_score=None,
    )
    batch = SimpleNamespace(items=[item], raw_response=SECRET_RAW)

    with caplog.at_level(logging.INFO, logger="syncscribe.workers.suggestion_mapper"):
        result = map_to_suggestions(
            batch,  # type: ignore[arg-type]
            uuid.uuid4(),
            uuid.uuid4(),
            document_text=SECRET_OLD_TEXT,
        )

    assert result == []
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "unknown_change_type=1" in logged
    assert SECRET_OLD_TEXT not in logged
    assert SECRET_RAW not in logged
    assert "персональными" not in logged
