"""最小对话回复 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill


class ConversationReplyAtomicSkill(BaseSkill):
    """生成普通对话回复。"""

    code = "conversation_reply_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = arguments.get("user_prompt", "")
        return {
            "reply": f"已收到你的请求：{user_prompt}。当前系统骨架已接入可扩展 Skill 执行链路。",
        }
