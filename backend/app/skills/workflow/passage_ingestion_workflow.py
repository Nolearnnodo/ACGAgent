"""古籍处理 Workflow（功能 A 主流水线）。

DAG：
  Stage 1  probe              （前置探针）
        ↓
  Stage 2  passage_meta       （Skill-1，文章基础信息）
        ↓
  Stage 3  person_layer       （Skill-2 + 3 + 6，人物层）
        ↓
  Stage 4  event_relation     （Skill-4 + 5 + 7，事件 + 历史关联 + 字母码）
        ↓
  Stage 5  format_output      （渲染 output/*.txt）

每个 Stage 完成后都会立刻向 SQLite 写一条 ExecutionStepRun，
前端 polling /passages/{id}/runs 即可实时看到 1/5、2/5、… 进度。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.agents.context import ExecutionContext
from app.db.session import SessionLocal
from app.models.execution import ExecutionStepRun
from app.skills.atomic.event_relation import EventRelationAtomicSkill
from app.skills.atomic.format_output import PassageFormatOutputAtomicSkill
from app.skills.atomic.passage_meta import PassageMetaAtomicSkill
from app.skills.atomic.person_layer import PersonLayerAtomicSkill
from app.skills.atomic.probe import ProbeAtomicSkill
from app.skills.base import BaseSkill


class PassageIngestionWorkflowSkill(BaseSkill):
    """功能 A 主流水线。"""

    code = "passage_ingestion_workflow"
    allowed_roles = ["user", "admin"]

    STAGES: list[tuple[str, str]] = [
        ("probe_atomic", "Stage 0 探针"),
        ("passage_meta_atomic", "Stage 1 文章信息"),
        ("person_layer_atomic", "Stage 2 人物层"),
        ("event_relation_atomic", "Stage 3 事件+关系"),
        ("passage_format_output_atomic", "Stage 4 渲染输出"),
    ]

    def __init__(self) -> None:
        self.skills = {
            "probe_atomic": ProbeAtomicSkill(),
            "passage_meta_atomic": PassageMetaAtomicSkill(),
            "person_layer_atomic": PersonLayerAtomicSkill(),
            "event_relation_atomic": EventRelationAtomicSkill(),
            "passage_format_output_atomic": PassageFormatOutputAtomicSkill(),
        }

    def _record_step(
        self,
        execution_run_id: int,
        step_no: int,
        skill_code: str,
        status: str,
        output: dict[str, Any] | None,
        error: str = "",
    ) -> None:
        """每个 Stage 完成/失败后立刻落一条 ExecutionStepRun。"""

        if execution_run_id is None:
            return  # 测试或独立调用，无 run id 时跳过持久化
        with SessionLocal() as db:
            db.add(
                ExecutionStepRun(
                    execution_run_id=execution_run_id,
                    step_no=step_no,
                    skill_code=skill_code,
                    status=status,
                    input_json="{}",
                    output_json=json.dumps(output or {}, ensure_ascii=False)[:8000],
                    error_message=(error or "")[:500],
                )
            )
            db.commit()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        execution_run_id: int | None = context.metadata.get("execution_run_id")
        results: dict[str, Any] = {}

        for idx, (skill_code, _label) in enumerate(self.STAGES, start=1):
            skill = self.skills[skill_code]
            try:
                output = skill.run(context, arguments)
                self._record_step(execution_run_id, idx, skill_code, "success", output)
                results[skill_code] = output
                context.set_step_result(f"step{idx}_result", {"stage": skill_code, "output": output})
            except Exception as exc:
                self._record_step(execution_run_id, idx, skill_code, "failed", None, str(exc))
                # 仍把异常向上抛，让 workflow 整体进入 partial/failed
                raise

        warning_count = len(context.metadata.get("warnings", []))
        overall_status = "success" if warning_count == 0 else "partial"
        format_output = results.get("passage_format_output_atomic", {})

        return {
            "reply": f"古籍处理流程已完成（{overall_status}, warnings={warning_count}）。",
            "status": overall_status,
            "output_path": format_output.get("output_path"),
            "stats": format_output.get("stats"),
            "warning_count": warning_count,
            "steps": {code: results.get(code) for code, _ in self.STAGES},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
