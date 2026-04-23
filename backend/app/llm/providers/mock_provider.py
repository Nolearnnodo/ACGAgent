"""默认 Mock Provider。

在没有真实供应商接入时，它使用规则化方式返回结果，便于本地联调。
"""

from typing import Any

from app.llm.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """一个简单的规则驱动 Provider。"""

    def generate_structured_intent(self, prompt: str, metadata: dict[str, Any]) -> dict[str, Any]:
        lower_prompt = prompt.lower()
        role = metadata.get("user_role", "user")

        if any(keyword in lower_prompt for keyword in ["写", "新增", "删除", "修改图", "写入图"]):
            target_skill = "graph_write_atomic"
            reason = "识别到可能涉及写操作，优先路由到写类 Skill。"
        elif any(keyword in lower_prompt for keyword in ["查询", "查找", "图谱", "关系", "节点"]):
            target_skill = "graph_query_atomic"
            reason = "识别到查询意图，路由到图查询 Skill。"
        else:
            target_skill = "conversation_reply_workflow"
            reason = "未识别到明确图操作，走对话回复 Workflow。"

        if target_skill == "graph_write_atomic" and role != "admin":
            return {
                "intent": "restricted_write_graph",
                "decision_type": "reject",
                "target_skill_code": "permission_denied",
                "reason": "当前用户不是管理员，禁止执行写类 Skill。",
                "arguments": {},
            }

        return {
            "intent": "general_request",
            "decision_type": "workflow" if target_skill.endswith("workflow") else "skill",
            "target_skill_code": target_skill,
            "reason": reason,
            "arguments": {"user_prompt": prompt},
        }

    def chat_completion(self, messages: list[dict[str, str]], metadata: dict[str, Any]) -> str:
        user_message = messages[-1]["content"] if messages else ""
        return f"这是一个占位回复。系统已收到你的请求：{user_message}"
