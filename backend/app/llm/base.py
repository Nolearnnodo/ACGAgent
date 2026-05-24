"""Unified external LLM provider abstraction."""

from abc import ABC, abstractmethod
from typing import Any

from app.observability.schemas import LLMCallResult, LLMUsage


class BaseLLMProvider(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    def generate_structured_intent(self, prompt: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Generate a structured planner decision."""

    @abstractmethod
    def chat_completion(self, messages: list[dict[str, str]], metadata: dict[str, Any]) -> str:
        """Generic chat completion interface."""

    def chat_completion_with_usage(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any],
    ) -> LLMCallResult:
        content = self.chat_completion(messages, metadata)
        return LLMCallResult(
            content=content,
            usage=LLMUsage(),
            provider="unknown",
            model="unknown",
        )
