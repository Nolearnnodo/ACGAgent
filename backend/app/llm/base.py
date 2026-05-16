"""外部 LLM 统一抽象。

首版目标不是做复杂推理，而是为 Planner 提供受控的结构化意图识别接口。
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseLLMProvider(ABC):
    """所有 LLM Provider 的统一接口。"""

    @abstractmethod
    def generate_structured_intent(self, prompt: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """生成结构化意图识别结果。"""

    @abstractmethod
    def chat_completion(self, messages: list[dict[str, str]], metadata: dict[str, Any]) -> str:
        """通用聊天接口，后续可供更丰富场景复用。"""
