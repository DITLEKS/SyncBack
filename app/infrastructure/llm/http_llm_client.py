"""Устаревшее имя. Оставлено для импортов; реализация — OpenAICompatibleClient."""

from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient

HttpLLMClient = OpenAICompatibleClient

__all__ = ["HttpLLMClient"]
