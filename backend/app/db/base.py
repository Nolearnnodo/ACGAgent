"""模型集中导入。

这个模块不再声明 Base，只负责确保所有模型在建表前被导入注册。
"""

from app.db.base_class import Base


# 导入模型以确保 Base.metadata.create_all 能发现它们。
from app.models.auth import AuthSession  # noqa: E402,F401
from app.models.conversation import Conversation, ConversationMemory, Message  # noqa: E402,F401
from app.models.dictionary import (  # noqa: E402,F401
    EraDictionary,
    HistoricalEventDictionary,
    RelationCodeDictionary,
    SourceTypeDictionary,
)
from app.models.execution import (  # noqa: E402,F401
    ExecutionRun,
    ExecutionStepRun,
    IdentityResolutionDecisionLog,
    PlannerDecisionRecord,
)
from app.models.observability import (  # noqa: E402,F401
    ExecutionTraceSummary,
    LLMCallLog,
    ModelPricingRule,
    ToolCallLog,
)
from app.models.passage import Passage  # noqa: E402,F401
from app.models.skill import SkillDefinition, SkillRelation  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401
