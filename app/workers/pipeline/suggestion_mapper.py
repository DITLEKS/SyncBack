import uuid
from app.domain.interfaces.llm_client import LLMSuggestionBatch
from app.infrastructure.db.models.enums import ChangeType
from app.infrastructure.db.models.suggestion import Suggestion

_CHANGE_TYPE_MAP = {"add": ChangeType.ADD, "modify": ChangeType.MODIFY, "delete": ChangeType.DELETE}


def map_to_suggestions(batch: LLMSuggestionBatch, analysis_job_id: uuid.UUID, source_reference: str | None) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    for item in batch.items:
        change_type = _CHANGE_TYPE_MAP.get(item.change_type)
        if change_type is None:
            continue
        suggestions.append(Suggestion(analysis_job_id=analysis_job_id, section_ref=item.section_ref, change_type=change_type, old_text=item.old_text, new_text=item.new_text, source_reference=source_reference))
    return suggestions
