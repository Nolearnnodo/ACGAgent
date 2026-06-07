"""Passage ingestion workflow.

The workflow runs the fixed Function A extraction pipeline, then appends the
Function B maintenance step: same-name person identity adjudication.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.context import ExecutionContext
from app.agents.step_output import serialize_step_output
from app.db.session import SessionLocal
from app.models.execution import ExecutionStepRun
from app.skills.atomic.event_relation import EventRelationAtomicSkill
from app.skills.atomic.format_output import PassageFormatOutputAtomicSkill
from app.skills.atomic.passage_meta import PassageMetaAtomicSkill
from app.skills.atomic.person_identity_resolution import PersonIdentityResolutionAtomicSkill
from app.skills.atomic.person_layer import PersonLayerAtomicSkill
from app.skills.atomic.probe import ProbeAtomicSkill
from app.skills.base import BaseSkill

_GRAPH_WRITE_WARNING_TYPES = {
    "neo4j_write_failed",
    "function_b_candidate_recall_failed",
    "function_b_identity_llm_failed",
    "function_b_full_text_llm_failed",
    "function_b_full_text_load_failed",
    "function_b_full_text_missing",
    "function_b_decision_log_failed",
    "function_b_identity_resolution_partial",
    "function_b_review_link_failed",
}


def _has_graph_write_warning(warnings: list[dict[str, Any]]) -> bool:
    """Only graph write issues should downgrade the overall workflow status."""

    for warning in warnings:
        warning_type = str(warning.get("type") or "").strip()
        if warning_type in _GRAPH_WRITE_WARNING_TYPES:
            return True
    return False


class PassageIngestionWorkflowSkill(BaseSkill):
    """Function A extraction workflow plus Function B identity resolution."""

    code = "passage_ingestion_workflow"
    allowed_roles = ["user", "admin"]

    STAGES: list[tuple[str, str]] = [
        ("probe_atomic", "Stage 0 probe"),
        ("passage_meta_atomic", "Stage 1 passage meta"),
        ("person_layer_atomic", "Stage 2 person layer"),
        ("event_relation_atomic", "Stage 3 event relation"),
        ("passage_format_output_atomic", "Stage 4 format output"),
        ("person_identity_resolution_atomic", "Stage 5 identity resolution"),
    ]

    def __init__(self) -> None:
        self.skills = {
            "probe_atomic": ProbeAtomicSkill(),
            "passage_meta_atomic": PassageMetaAtomicSkill(),
            "person_layer_atomic": PersonLayerAtomicSkill(),
            "event_relation_atomic": EventRelationAtomicSkill(),
            "passage_format_output_atomic": PassageFormatOutputAtomicSkill(),
            "person_identity_resolution_atomic": PersonIdentityResolutionAtomicSkill(),
        }

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
        """Persist one step result immediately for frontend polling."""

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
        """Create a running step before LLM calls so traces can point to it."""

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

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        execution_run_id: int | None = context.metadata.get("execution_run_id")
        results: dict[str, Any] = {}

        for idx, (skill_code, _label) in enumerate(self.STAGES, start=1):
            skill = self.skills[skill_code]
            execution_step_run_id = self._start_step(execution_run_id, idx, skill_code)
            previous_step_run_id = context.metadata.get("execution_step_run_id")
            context.metadata["execution_step_run_id"] = execution_step_run_id
            try:
                output = skill.run(context, arguments)
                self._record_step(
                    execution_run_id,
                    execution_step_run_id,
                    idx,
                    skill_code,
                    "success",
                    output,
                )
                results[skill_code] = output
                context.set_step_result(
                    f"step{idx}_result",
                    {"stage": skill_code, "output": output},
                )
            except Exception as exc:
                self._record_step(
                    execution_run_id,
                    execution_step_run_id,
                    idx,
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

        warnings = context.metadata.get("warnings", [])
        warning_count = len(warnings)
        overall_status = "partial" if _has_graph_write_warning(warnings) else "success"
        format_output = results.get("passage_format_output_atomic", {})
        identity_resolution_output = results.get("person_identity_resolution_atomic", {})

        return {
            "reply": f"Passage workflow finished ({overall_status}, warnings={warning_count}).",
            "status": overall_status,
            "output_path": format_output.get("output_path"),
            "stats": format_output.get("stats"),
            "warning_count": warning_count,
            "function_b": {
                "identity_resolution": identity_resolution_output,
            },
            "steps": {code: results.get(code) for code, _ in self.STAGES},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
