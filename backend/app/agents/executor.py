"""Executor 模块。"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agents.context import ExecutionContext
from app.agents.models import PlannerDecision, StepExecutionResult
from app.models.execution import ExecutionRun, ExecutionStepRun
from app.models.skill import SkillDefinition
from app.skills.loader import load_skill_registry


class Executor:
    """执行已被 Planner 选中的 Skill / Workflow。"""

    def __init__(self) -> None:
        self.registry = load_skill_registry()

    def execute(
        self,
        db: Session,
        context: ExecutionContext,
        planner_decision: PlannerDecision,
        planner_record_id: int | None,
        trigger_message_id: int | None,
    ) -> StepExecutionResult:
        """执行一个结构化决策。"""

        # 无论是 chat 还是 passage 触发，这里都先落一条运行记录，
        # 后续的步骤状态、失败原因和产出都统一挂在这条 run 下，便于审计与监控。
        execution_run = ExecutionRun(
            conversation_id=context.conversation.get("id"),
            trigger_message_id=trigger_message_id,
            planner_decision_id=planner_record_id,
            trigger_type=context.metadata.get("trigger_type", "chat"),
            passage_id=context.metadata.get("passage_id"),
            status="running",
        )
        db.add(execution_run)
        db.commit()
        db.refresh(execution_run)

        if planner_decision.decision_type == "reject":
            # reject 表示在真正执行 Skill 之前就被上游策略拒绝。
            # 这里仍然写入 step 记录，确保前端和后续排障能看到“为什么没执行”。
            result = StepExecutionResult(
                step_no=1,
                skill_code=planner_decision.target_skill_code,
                success=False,
                output={"message": planner_decision.reason},
                error_message=planner_decision.reason,
            )
            execution_run.status = "failed"
            execution_run.finished_at = datetime.now(timezone.utc)
            db.add(
                ExecutionStepRun(
                    execution_run_id=execution_run.id,
                    step_no=1,
                    skill_code=planner_decision.target_skill_code,
                    status="failed",
                    input_json=json.dumps(planner_decision.arguments, ensure_ascii=False),
                    output_json=json.dumps(result.output, ensure_ascii=False),
                    error_message=result.error_message,
                )
            )
            db.commit()
            return result

        skill = self.registry.get(planner_decision.target_skill_code)
        metadata = db.query(SkillDefinition).filter(SkillDefinition.code == planner_decision.target_skill_code).first()

        # 权限优先读取数据库中的 metadata 配置；
        # 如果 metadata 缺失或 JSON 损坏，再回退到代码内置 allowed_roles，
        # 这样能兼顾“配置可变更”与“运行时不至于完全失效”。
        allowed_roles = skill.allowed_roles
        if metadata and metadata.allowed_roles_json:
            try:
                allowed_roles = json.loads(metadata.allowed_roles_json)
            except json.JSONDecodeError:
                allowed_roles = skill.allowed_roles

        if context.user["role"] not in allowed_roles:
            # 即便 Planner 已做过一次约束，这里仍然必须再次校验角色。
            # Executor 是最终执行入口，二次校验可以防止前端绕过或上游决策异常。
            result = StepExecutionResult(
                step_no=1,
                skill_code=planner_decision.target_skill_code,
                success=False,
                output={"message": "当前用户无权执行该 Skill。"},
                error_message="permission denied",
            )
            execution_run.status = "failed"
            execution_run.finished_at = datetime.now(timezone.utc)
            db.add(
                ExecutionStepRun(
                    execution_run_id=execution_run.id,
                    step_no=1,
                    skill_code=planner_decision.target_skill_code,
                    status="failed",
                    input_json=json.dumps(planner_decision.arguments, ensure_ascii=False),
                    output_json=json.dumps(result.output, ensure_ascii=False),
                    error_message=result.error_message,
                )
            )
            db.commit()
            return result

        # 把 execution_run_id 注入 metadata，让 workflow 可以分阶段写 step_run。
        context.metadata["execution_run_id"] = execution_run.id

        is_workflow = planner_decision.target_skill_code.endswith("_workflow")

        output = skill.run(context, planner_decision.arguments)
        # 统一把当前 step 的结果写入 ExecutionContext，
        # 后续 Workflow 或更复杂的多步执行可以通过 step1_result 等键继续引用。
        context.set_step_result("step1_result", output)

        step_result = StepExecutionResult(
            step_no=1,
            skill_code=planner_decision.target_skill_code,
            success=True,
            output=output,
        )
        if not is_workflow:
            # atomic 直接由 executor 兜底写 step_run；
            # workflow 自行管理子步骤（step_no 1..N），避免重复记录。
            db.add(
                ExecutionStepRun(
                    execution_run_id=execution_run.id,
                    step_no=1,
                    skill_code=planner_decision.target_skill_code,
                    status="success",
                    input_json=json.dumps(planner_decision.arguments, ensure_ascii=False),
                    output_json=json.dumps(output, ensure_ascii=False),
                    error_message="",
                )
            )
        execution_run.status = "success"
        execution_run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return step_result
