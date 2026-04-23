"""Planner 模块。

Planner 只负责在受控范围内把输入映射到既有 Skill / Workflow，不能自由生成执行计划。
"""

from app.agents.models import PlannerDecision, PlannerInput
from app.llm.providers.factory import get_llm_provider


class Planner:
    """受控 Planner。"""

    def __init__(self) -> None:
        self.provider = get_llm_provider()

    def plan(self, planner_input: PlannerInput) -> PlannerDecision:
        """根据用户输入与最近上下文生成结构化决策。"""

        provider_result = self.provider.generate_structured_intent(
            prompt=planner_input.user_input,
            metadata={
                "user_id": planner_input.user_id,
                "user_role": planner_input.user_role,
                "conversation_id": planner_input.conversation_id,
                "recent_messages": planner_input.recent_messages,
            },
        )
        return PlannerDecision(
            intent=provider_result["intent"],
            decision_type=provider_result["decision_type"],
            target_skill_code=provider_result["target_skill_code"],
            reason=provider_result["reason"],
            arguments=provider_result.get("arguments", {}),
        )
