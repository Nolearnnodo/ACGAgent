"""DeepSeek Provider.

The planner still has a safe mock fallback, but normal chat/structured calls
raise provider errors so extraction warnings preserve the real failure cause.
"""

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm.base import BaseLLMProvider
from app.llm.providers.mock_provider import MockLLMProvider


class DeepSeekProvider(BaseLLMProvider):
    """基于 DeepSeek API 的 Provider。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.mock_provider = MockLLMProvider()

    def _build_headers(self) -> dict[str, str]:
        """构造请求头。"""

        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat_completion(self, messages: list[dict[str, str]]) -> str:
        """调用 DeepSeek chat completions 接口。"""

        if not self.settings.llm_api_base_url or not self.settings.llm_api_key:
            raise ValueError("DeepSeek API 配置不完整。")

        response = httpx.post(
            f"{self.settings.llm_api_base_url.rstrip('/')}/chat/completions",
            headers=self._build_headers(),
            json={
                "model": self.settings.llm_model_name,
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=self.settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def generate_structured_intent(self, prompt: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """用 DeepSeek 生成结构化意图识别结果。"""

        allowed_skill_codes = [
            "conversation_reply_workflow",
            "graph_query_atomic",
            "graph_write_atomic",
            "query_workflow",
        ]
        system_prompt = (
            "你是一个受控 Planner，只能从给定 Skill 中选择一个执行目标，"
            "不能自由生成多步计划。"
            "请严格返回 JSON 对象，包含以下字段："
            "intent, decision_type, target_skill_code, reason, arguments。"
            "decision_type 只能是 skill、workflow、reject。"
            f"可选 target_skill_code 仅允许：{', '.join(allowed_skill_codes)}。"
            "如果用户请求涉及图写入且用户不是 admin，则返回 reject。"
            "如果用户请求是查询类（如'XXX是谁'、'XXX和YYY有什么关系'、'图中有多少XXX'），"
            "请路由到 query_workflow。"
        )
        user_prompt = {
            "user_prompt": prompt,
            "user_role": metadata.get("user_role", "user"),
            "recent_messages": metadata.get("recent_messages", []),
        }

        try:
            content = self._post_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                ]
            )
            result = json.loads(content)

            required_keys = {"intent", "decision_type", "target_skill_code", "reason", "arguments"}
            if not required_keys.issubset(result):
                raise ValueError("DeepSeek 返回缺少必要字段。")
            if result["decision_type"] not in {"skill", "workflow", "reject"}:
                raise ValueError("DeepSeek 返回了无效 decision_type。")
            if result["target_skill_code"] not in allowed_skill_codes and result["decision_type"] != "reject":
                raise ValueError("DeepSeek 返回了未注册的 skill code。")

            if result["decision_type"] == "reject":
                result["target_skill_code"] = "permission_denied"

            result["arguments"] = result.get("arguments") or {"user_prompt": prompt}
            if "user_prompt" not in result["arguments"]:
                result["arguments"]["user_prompt"] = prompt
            return result
        except Exception:
            # 出现解析失败、配置缺失、网络异常时，退回到规则化 mock，确保系统可用。
            return self.mock_provider.generate_structured_intent(prompt, metadata)

    def chat_completion(self, messages: list[dict[str, str]], metadata: dict[str, Any]) -> str:
        """Call DeepSeek and let callers record the real provider error."""

        return self._post_chat_completion(messages)
