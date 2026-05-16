"""LLM Provider 工厂。"""

from app.core.config import get_settings
from app.llm.base import BaseLLMProvider
from app.llm.providers.deepseek_provider import DeepSeekProvider
from app.llm.providers.mock_provider import MockLLMProvider


def get_llm_provider() -> BaseLLMProvider:
    """根据配置返回当前 Provider。"""

    settings = get_settings()

    # 首版仅内置 mock provider，但接口已经预留多供应商扩展点。
    if settings.llm_provider == "mock":
        return MockLLMProvider()

    if settings.llm_provider == "deepseek":
        return DeepSeekProvider()

    return MockLLMProvider()
