"""对话回复 Workflow Skill。"""

from app.agents.context import ExecutionContext
from app.skills.atomic.conversation_reply import ConversationReplyAtomicSkill
from app.skills.base import BaseSkill


class ConversationReplyWorkflowSkill(BaseSkill):
    """最小 Workflow 示例。

    虽然当前只有一步，但保持 Workflow 结构，便于未来扩展为多步骤固定流程。
    """

    code = "conversation_reply_workflow"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.atomic_skill = ConversationReplyAtomicSkill()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        return self.atomic_skill.run(context, arguments)
