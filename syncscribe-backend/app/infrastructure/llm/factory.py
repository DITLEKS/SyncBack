"""Выбор реализации LLMClient по LLM_PROVIDER: stub / remote_http / onprem."""
from app.core.config import Settings, get_settings
from app.infrastructure.llm.http_llm_client import HttpLLMClient
from app.infrastructure.llm.on_prem_client import OnPremLLMClient
from app.infrastructure.llm.stub_client import StubLLMClient


def get_llm_client(settings: Settings | None = None):
    settings = settings or get_settings()
    if settings.llm_provider == "stub":
        return StubLLMClient()
    if settings.llm_provider == "remote_http":
        return HttpLLMClient(settings)
    if settings.llm_provider == "onprem":
        return OnPremLLMClient(settings)
    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
