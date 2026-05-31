"""查询 Workflow Skill。

三步流程：意图分类 → 专项查询 → 答案组织。
"""

from typing import Any

from app.agents.context import ExecutionContext
from app.agents.step_output import serialize_step_output
from app.db.session import SessionLocal
from app.models.execution import ExecutionStepRun
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

    def _record_step(
        self,
        execution_run_id: int | None,
        execution_step_run_id: int | None,
        step_no: int,
        skill_code: str,
        status: str,
        output: dict[str, Any] | None,
        error: str = "",
    ) -> None:
        if execution_run_id is None:
            return

        with SessionLocal() as db:
            step = (
                db.get(ExecutionStepRun, execution_step_run_id)
                if execution_step_run_id is not None
                else None
            )
            if step is None:
                step = ExecutionStepRun(
                    execution_run_id=execution_run_id,
                    step_no=step_no,
                    skill_code=skill_code,
                    input_json="{}",
                )
            step.status = status
            step.output_json = serialize_step_output(output)
            step.error_message = (error or "")[:500]
            db.add(step)
            db.commit()

    def _start_step(
        self,
        execution_run_id: int | None,
        step_no: int,
        skill_code: str,
    ) -> int | None:
        if execution_run_id is None:
            return None

        with SessionLocal() as db:
            step = ExecutionStepRun(
                execution_run_id=execution_run_id,
                step_no=step_no,
                skill_code=skill_code,
                status="running",
                input_json="{}",
                output_json="{}",
                error_message="",
            )
            db.add(step)
            db.commit()
            db.refresh(step)
            return step.id

    def _run_step(
        self,
        context: ExecutionContext,
        execution_run_id: int | None,
        step_no: int,
        skill_code: str,
        skill: BaseSkill,
        arguments: dict,
    ) -> dict:
        execution_step_run_id = self._start_step(execution_run_id, step_no, skill_code)
        previous_step_run_id = context.metadata.get("execution_step_run_id")
        previous_skill_code = context.metadata.get("skill_code")
        context.metadata["execution_step_run_id"] = execution_step_run_id
        context.metadata["skill_code"] = skill_code
        try:
            output = skill.run(context, arguments)
            self._record_step(
                execution_run_id,
                execution_step_run_id,
                step_no,
                skill_code,
                "success",
                output,
            )
            return output
        except Exception as exc:
            self._record_step(
                execution_run_id,
                execution_step_run_id,
                step_no,
                skill_code,
                "failed",
                None,
                str(exc),
            )
            raise
        finally:
            if previous_step_run_id is None:
                context.metadata.pop("execution_step_run_id", None)
            else:
                context.metadata["execution_step_run_id"] = previous_step_run_id
            if previous_skill_code is None:
                context.metadata.pop("skill_code", None)
            else:
                context.metadata["skill_code"] = previous_skill_code

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        execution_run_id: int | None = context.metadata.get("execution_run_id")
        user_prompt = arguments["user_prompt"]

        step1_result = self._run_step(
            context,
            execution_run_id,
            1,
            self.intent_classifier.code,
            self.intent_classifier,
            {
                "user_prompt": user_prompt,
                "recent_messages": arguments.get("recent_messages", []),
            },
        )
        context.set_step_result("step1_result", step1_result)

        query_type = step1_result.get("query_type", "")
        extracted_params = step1_result.get("extracted_params", {})

        if query_type == "person_info":
            query_skill = self.person_info
            query_arguments = {
                "person_name": extracted_params.get("person_name", user_prompt)
            }
        elif query_type == "person_relation":
            query_skill = self.person_relation
            query_arguments = {
                "person_a": extracted_params.get("person_a", ""),
                "person_b": extracted_params.get("person_b", ""),
            }
        elif query_type == "graph_statistics":
            query_skill = self.graph_statistics
            query_arguments = {
                "keyword": extracted_params.get("keyword", ""),
                "stat_description": extracted_params.get(
                    "stat_description", user_prompt
                ),
            }
        else:
            query_skill = self.graph_statistics
            query_arguments = {
                "keyword": "",
                "stat_description": user_prompt,
            }

        step2_result = self._run_step(
            context,
            execution_run_id,
            2,
            query_skill.code,
            query_skill,
            query_arguments,
        )
        context.set_step_result("step2_result", step2_result)

        step3_result = self._run_step(
            context,
            execution_run_id,
            3,
            self.answer_compose.code,
            self.answer_compose,
            {
                "user_prompt": user_prompt,
                "query_type": query_type,
                "query_result": step2_result,
                "source": step2_result.get("source", "graph"),
            },
        )
        context.set_step_result("step3_result", step3_result)

        return step3_result
