"""查询 Workflow Skill。

三步流程：意图分类 → 专项查询 → 答案组织。
"""

from app.agents.context import ExecutionContext
from app.skills.atomic.graph_statistics_query import GraphStatisticsQueryAtomicSkill
from app.skills.atomic.person_info_query import PersonInfoQueryAtomicSkill
from app.skills.atomic.person_relation_query import PersonRelationQueryAtomicSkill
from app.skills.atomic.query_answer_compose import QueryAnswerComposeAtomicSkill
from app.skills.atomic.query_intent_classifier import QueryIntentClassifierAtomicSkill
from app.skills.base import BaseSkill


class QueryWorkflowSkill(BaseSkill):
    """三步查询 Workflow：意图分类 → 专项查询 → 答案组织。"""

    code = "query_workflow"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.intent_classifier = QueryIntentClassifierAtomicSkill()
        self.answer_compose = QueryAnswerComposeAtomicSkill()
        self.person_info = PersonInfoQueryAtomicSkill()
        self.person_relation = PersonRelationQueryAtomicSkill()
        self.graph_statistics = GraphStatisticsQueryAtomicSkill()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = arguments["user_prompt"]

        # Step 1: 意图分类
        step1_result = self.intent_classifier.run(
            context,
            {
                "user_prompt": user_prompt,
                "recent_messages": arguments.get("recent_messages", []),
            },
        )
        context.set_step_result("step1_result", step1_result)

        query_type = step1_result.get("query_type", "")
        extracted_params = step1_result.get("extracted_params", {})

        # Step 2: 专项查询
        if query_type == "person_info":
            step2_result = self.person_info.run(
                context,
                {"person_name": extracted_params.get("person_name", user_prompt)},
            )
        elif query_type == "person_relation":
            step2_result = self.person_relation.run(
                context,
                {
                    "person_a": extracted_params.get("person_a", ""),
                    "person_b": extracted_params.get("person_b", ""),
                },
            )
        elif query_type == "graph_statistics":
            step2_result = self.graph_statistics.run(
                context,
                {
                    "keyword": extracted_params.get("keyword", ""),
                    "stat_description": extracted_params.get(
                        "stat_description", user_prompt
                    ),
                },
            )
        else:
            # 兜底：当意图无法识别时，将原始提示作为统计描述执行
            step2_result = self.graph_statistics.run(
                context,
                {
                    "keyword": "",
                    "stat_description": user_prompt,
                },
            )
        context.set_step_result("step2_result", step2_result)

        # Step 3: 答案组织
        step3_result = self.answer_compose.run(
            context,
            {
                "user_prompt": user_prompt,
                "query_type": query_type,
                "query_result": step2_result,
                "source": step2_result.get("source", "graph"),
            },
        )
        context.set_step_result("step3_result", step3_result)

        return step3_result
