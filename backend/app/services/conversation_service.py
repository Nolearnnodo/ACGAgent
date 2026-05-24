"""对话服务。"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, selectinload

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerInput
from app.agents.planner import Planner
from app.core.config import get_settings
from app.llm.providers.factory import get_llm_provider
from app.models.conversation import Conversation, ConversationMemory, Message
from app.models.execution import PlannerDecisionRecord
from app.models.user import User
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


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
        """读取会话详情（同时预加载 messages 与 memory）。"""

        conversation = (
            self.db.query(Conversation)
            .options(
                selectinload(Conversation.messages),
                selectinload(Conversation.memory),
            )
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

        # ── 滑动窗口摘要压缩 ──────────────────────────────────────────────────
        memory: ConversationMemory | None = conversation.memory
        window_size: int = memory.window_size if memory else self.settings.conversation_memory_window

        # conversation.messages 此时尚不包含刚提交的 user_message（ORM 对象层）
        all_messages = conversation.messages
        current_summary: str = memory.summary if memory else ""

        if len(all_messages) > window_size:
            # 被滑出窗口的消息（最旧的那些）
            evicted_messages = all_messages[: len(all_messages) - window_size]

            # 构造压缩提示：旧摘要 + 被滑出的消息 → 新摘要
            compress_prompt_parts: list[str] = []
            if current_summary:
                compress_prompt_parts.append(f"已有摘要：{current_summary}")
            compress_prompt_parts.append("新增对话（需纳入摘要）：")
            for msg in evicted_messages:
                role_label = "用户" if msg.role == "user" else "助手"
                compress_prompt_parts.append(f"[{role_label}] {msg.content}")

            compress_messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个对话摘要助手。请将下方对话历史压缩为 1-2 句简洁的中文关键信息，"
                        "保留对后续对话最有价值的内容，去除冗余细节。只输出摘要本身，不要加任何前缀。"
                    ),
                },
                {"role": "user", "content": "\n".join(compress_prompt_parts)},
            ]

            try:
                llm = get_llm_provider()
                new_summary = llm.chat_completion(
                    messages=compress_messages,
                    metadata={"purpose": "conversation_summary", "conversation_id": conversation.id},
                ).strip()

                if memory is not None:
                    memory.summary = new_summary
                    memory.last_message_id = evicted_messages[-1].id
                    memory.updated_at = datetime.now(timezone.utc)
                    self.db.add(memory)
                    self.db.commit()
                    current_summary = new_summary
                    logger.info("会话 %d 摘要已更新，压缩 %d 条消息。", conversation.id, len(evicted_messages))
            except Exception:
                logger.exception("会话 %d 摘要压缩失败，保留旧摘要继续执行。", conversation.id)
                # 失败时 current_summary 保持旧值，不中断主流程

        # ── 构造 recent_messages（含摘要前缀）────────────────────────────────
        recent_messages: list[dict[str, str]] = []
        if current_summary:
            recent_messages.append({"role": "system", "content": f"对话历史摘要：{current_summary}"})

        recent_messages.extend(
            {"role": message.role, "content": message.content}
            for message in all_messages[-window_size:]
        )
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
            metadata={
                "memory_window": self.settings.conversation_memory_window,
                "trigger_type": "chat",
                "conversation_id": conversation.id,
                "message_id": user_message.id,
            },
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
