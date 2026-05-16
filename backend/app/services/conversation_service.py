"""对话服务。"""

from sqlalchemy.orm import Session, selectinload

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerInput
from app.agents.planner import Planner
from app.core.config import get_settings
from app.models.conversation import Conversation, ConversationMemory, Message
from app.models.execution import PlannerDecisionRecord
from app.models.user import User
from app.skills.registry import SkillRegistry


class ConversationService:
    """负责会话、消息与 Agent 执行编排。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.planner = Planner()
        self.executor = Executor()
        self.skill_registry = SkillRegistry()
        self.skill_registry.register_builtin_metadata(db)

    def list_conversations(self, user: User) -> list[Conversation]:
        """列出用户会话。"""

        return (
            self.db.query(Conversation)
            .filter(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    def create_conversation(self, user: User, title: str) -> Conversation:
        """创建新会话。"""

        conversation = Conversation(user_id=user.id, title=title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        self.db.add(
            ConversationMemory(
                conversation_id=conversation.id,
                summary="",
                window_size=self.settings.conversation_memory_window,
            )
        )
        self.db.commit()
        return conversation

    def get_conversation_detail(self, user: User, conversation_id: int) -> Conversation:
        """读取会话详情。"""

        conversation = (
            self.db.query(Conversation)
            .options(selectinload(Conversation.messages))
            .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
            .first()
        )
        if conversation is None:
            raise ValueError("会话不存在。")
        return conversation

    def rename_conversation(self, user: User, conversation_id: int, title: str) -> Conversation:
        """重命名会话标题。"""

        conversation = self.get_conversation_detail(user, conversation_id)
        conversation.title = title
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def delete_conversation(self, user: User, conversation_id: int) -> None:
        """删除会话及其关联数据。"""

        conversation = (
            self.db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user.id)
            .first()
        )
        if conversation is None:
            raise ValueError("会话不存在。")

        self.db.delete(conversation)
        self.db.commit()

    def add_user_message_and_execute(self, user: User, conversation_id: int, content: str) -> tuple[Conversation, Message, Message]:
        """写入用户消息，并触发 Planner + Executor。"""

        conversation = self.get_conversation_detail(user, conversation_id)
        next_sequence = len(conversation.messages) + 1

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=content,
            sequence=next_sequence,
        )
        self.db.add(user_message)
        self.db.commit()
        self.db.refresh(user_message)

        recent_messages = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages[-self.settings.conversation_memory_window :]
        ]
        recent_messages.append({"role": "user", "content": content})

        planner_decision = self.planner.plan(
            PlannerInput(
                user_input=content,
                user_id=user.id,
                user_role=user.role,
                conversation_id=conversation.id,
                recent_messages=recent_messages,
            )
        )

        planner_record = PlannerDecisionRecord(
            conversation_id=conversation.id,
            message_id=user_message.id,
            intent=planner_decision.intent,
            decision_type=planner_decision.decision_type,
            target_skill_code=planner_decision.target_skill_code,
            reason=planner_decision.reason,
        )
        self.db.add(planner_record)
        self.db.commit()
        self.db.refresh(planner_record)

        context = ExecutionContext(
            user={"id": user.id, "role": user.role, "email": user.email},
            conversation={"id": conversation.id, "title": conversation.title},
            metadata={"memory_window": self.settings.conversation_memory_window},
        )
        result = self.executor.execute(
            db=self.db,
            context=context,
            planner_decision=planner_decision,
            planner_record_id=planner_record.id,
            trigger_message_id=user_message.id,
        )

        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=result.output.get("reply") or result.output.get("message") or str(result.output),
            sequence=next_sequence + 1,
        )
        self.db.add(assistant_message)
        self.db.commit()
        self.db.refresh(assistant_message)
        self.db.refresh(conversation)
        return conversation, user_message, assistant_message
