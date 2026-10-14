"""Устаревшее имя. On-prem движки (vLLM, Ollama, TGI) говорят на OpenAI-совместимом API,
поэтому отдельный клиент не нужен — достаточно LLM_ENDPOINT и LLM_AUTH=none."""

from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient

OnPremLLMClient = OpenAICompatibleClient

__all__ = ["OnPremLLMClient"]
