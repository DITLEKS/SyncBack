"""Выбор реализации LLMClient по LLM_PROVIDER."""

from app.core.config import Settings, get_settings
from app.domain.interfaces.llm_client import LLMClient
from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient
from app.infrastructure.llm.stub_client import StubLLMClient


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Фабрика LLMClient по значению LLM_PROVIDER из настроек.

      'stub'              -> StubLLMClient (тесты и локальная разработка без модели)
      'openai_compatible' -> OpenAICompatibleClient (любой OpenAI-совместимый API:
                             локальный Ollama/vLLM, внешний сервис, GigaChat)
      'remote_http', 'onprem' -> то же, что openai_compatible (устаревшие алиасы)

    Возвращаемый тип — LLMClient (Protocol из domain/interfaces), чтобы проверять
    совместимость реализаций со структурным контрактом.
    """
    settings = settings or get_settings()
    if settings.llm_provider == "stub":
        return StubLLMClient()
    if settings.llm_provider in ("openai_compatible", "remote_http", "onprem"):
        return OpenAICompatibleClient(settings)
    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider}")
