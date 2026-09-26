"""Выбор реализации LLMClient по LLM_PROVIDER: stub / remote_http / onprem."""
from app.core.config import Settings, get_settings
from app.domain.interfaces.llm_client import LLMClient
from app.infrastructure.llm.http_llm_client import HttpLLMClient
from app.infrastructure.llm.on_prem_client import OnPremLLMClient
from app.infrastructure.llm.stub_client import StubLLMClient


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Фабрика LLMClient по значению LLM_PROVIDER из настроек.

    Возвращаемый тип аннотирован как LLMClient (Protocol из domain/interfaces),
    чтобы mypy/pyright проверяли совместимость всех реализаций со структурным контрактом.
    Конкретный класс выбирается по settings.llm_provider:
      'stub'        -> StubLLMClient     (для тестов и локальной разработки)
      'remote_http' -> HttpLLMClient     (внешний LLM-сервис по HTTP)
      'onprem'      -> OnPremLLMClient   (on-prem инсталляция)
    """
    settings = settings or get_settings()
    if settings.llm_provider == "stub":
        return StubLLMClient()
    if settings.llm_provider == "remote_http":
        return HttpLLMClient(settings)
    if settings.llm_provider == "onprem":
        return OnPremLLMClient(settings)
    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
