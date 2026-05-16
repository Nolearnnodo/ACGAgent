"""Passage ingestion workflow.

The workflow runs the fixed Function A extraction pipeline, then appends the
current minimal Function B maintenance step: exact-name person merging.
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
from app.skills.atomic.person_exact_match_merge import PersonExactMatchMergeAtomicSkill
from app.skills.atomic.person_layer import PersonLayerAtomicSkill
from app.skills.atomic.probe import ProbeAtomicSkill
from app.skills.base import BaseSkill

_GRAPH_WRITE_WARNING_TYPES = {
    "neo4j_write_failed",
    "function_b_exact_merge_failed",
    "function_b_exact_merge_partial",
}


def _has_graph_write_warning(warnings: list[dict[str, Any]]) -> bool:
    """Only graph write issues should downgrade the overall workflow status."""

    for warning in warnings:
        warning_type = str(warning.get("type") or "").strip()
        if warning_type in _GRAPH_WRITE_WARNING_TYPES:
            return True
    return False


class PassageIngestionWorkflowSkill(BaseSkill):
    """Function A extraction workflow plus the first Function B maintenance step."""

    code = "passage_ingestion_workflow"
    allowed_roles = ["user", "admin"]

    STAGES: list[tuple[str, str]] = [
        ("probe_atomic", "Stage 0 probe"),
        ("passage_meta_atomic", "Stage 1 passage meta"),
        ("person_layer_atomic", "Stage 2 person layer"),
        ("event_relation_atomic", "Stage 3 event relation"),
        ("passage_format_output_atomic", "Stage 4 format output"),
        ("person_exact_match_merge_atomic", "Stage 5 exact-name merge"),
    ]

    def __init__(self) -> None:
        self.skills = {
            "probe_atomic": ProbeAtomicSkill(),
            "passage_meta_atomic": PassageMetaAtomicSkill(),
            "person_layer_atomic": PersonLayerAtomicSkill(),
            "event_relation_atomic": EventRelationAtomicSkill(),
            "passage_format_output_atomic": PassageFormatOutputAtomicSkill(),
            "person_exact_match_merge_atomic": PersonExactMatchMergeAtomicSkill(),
        }

    def _record_step(
        self,
        execution_run_id: int | None,
        step_no: int,
        skill_code: str,
        status: str,
        output: dict[str, Any] | None,
        error: str = "",
    ) -> None:
        """Persist one step result immediately for frontend polling."""

        if execution_run_id is None:
            return

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
                context.set_step_result(
                    f"step{idx}_result",
                    {"stage": skill_code, "output": output},
                )
            except Exception as exc:
                self._record_step(execution_run_id, idx, skill_code, "failed", None, str(exc))
                raise

        warnings = context.metadata.get("warnings", [])
        warning_count = len(warnings)
        overall_status = "partial" if _has_graph_write_warning(warnings) else "success"
        format_output = results.get("passage_format_output_atomic", {})
        exact_merge_output = results.get("person_exact_match_merge_atomic", {})

        return {
            "reply": f"Passage workflow finished ({overall_status}, warnings={warning_count}).",
            "status": overall_status,
            "output_path": format_output.get("output_path"),
            "stats": format_output.get("stats"),
            "warning_count": warning_count,
            "function_b": {
                "exact_name_merge": exact_merge_output,
            },
            "steps": {code: results.get(code) for code, _ in self.STAGES},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
