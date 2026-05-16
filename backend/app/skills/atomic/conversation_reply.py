"""最小对话回复 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.llm.providers.factory import get_llm_provider
from app.skills.base import BaseSkill


class ConversationReplyAtomicSkill(BaseSkill):
    """生成普通对话回复。"""

    code = "conversation_reply_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.provider = get_llm_provider()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = str(arguments.get("user_prompt", "")).strip()
        recent_messages = arguments.get("recent_messages") or []

        # 对话 Skill 只负责把当前上下文标准化后交给统一 LLM Provider，
        # 具体调用哪个模型由配置文件和工厂决定，而不是写死在 Skill 中。
        messages = [
            {
                "role": "system",
                "content": (
                    "你是图数据库维护系统中的对话助手。"
                    "请基于用户输入给出清晰、简洁、中文的回复。"
                ),
            }
        ]

        for message in recent_messages:
            role = message.get("role")
            content = message.get("content")
            if role in {"system", "user", "assistant"} and content:
                messages.append({"role": role, "content": str(content)})

        messages.append({"role": "user", "content": user_prompt})
        reply = self.provider.chat_completion(
            messages,
            metadata={
                "skill_code": self.code,
                "user": context.user,
                "conversation": context.conversation,
            },
        )

        return {
            "reply": reply,
        }
