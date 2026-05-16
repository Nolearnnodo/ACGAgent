"""Agent 运行时数据模型。

这些模型不直接映射数据库，而是作为 Planner / Executor 之间的结构化协议。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlannerInput:
    """Planner 接收到的上下文。"""

    user_input: str
    user_id: int
    user_role: str
    conversation_id: int
    recent_messages: list[dict[str, Any]]


@dataclass(slots=True)
class PlannerDecision:
    """Planner 输出的结构化决策。"""

    intent: str
    decision_type: str
    target_skill_code: str
    reason: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepExecutionResult:
    """单步执行结果。"""

    step_no: int
    skill_code: str
    success: bool
    output: dict[str, Any]
    error_message: str = ""
