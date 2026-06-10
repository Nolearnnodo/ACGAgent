"""定向重跑单篇文章的同名人物裁定步骤。"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy.orm import Session

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision, StepExecutionResult
from app.db.session import SessionLocal
from app.models.passage import Passage
from app.models.user import User


def rerun_identity_resolution(
    db: Session,
    passage: Passage,
    user: User,
    executor: Executor | None = None,
    new_person_id: int | None = None,
    candidate_person_id: int | None = None,
) -> StepExecutionResult:
    """Run only Function B identity resolution and update the passage status."""

    if (new_person_id is None) != (candidate_person_id is None):
        raise ValueError(
            "new_person_id and candidate_person_id must be provided together."
        )
    executor = executor or Executor()
    original_status = passage.workflow_status
    context = ExecutionContext(
        user={"id": user.id, "role": user.role, "email": user.email},
        conversation={},
        metadata={
            "trigger_type": "identity_resolution_rerun",
            "passage_id": passage.doc_id,
            "passage": {
                "doc_id": passage.doc_id,
                "title": passage.title,
                "context": passage.context,
                "source_type": passage.source_type,
                "source_file": passage.file_name or "",
            },
        },
    )
    arguments = {"doc_id": passage.doc_id, "title": passage.title}
    if new_person_id is not None:
        arguments.update(
            {
                "new_person_id": new_person_id,
                "candidate_person_id": candidate_person_id,
            }
        )
    result = executor.execute(
        db=db,
        context=context,
        planner_decision=PlannerDecision(
            intent="rerun_identity_resolution",
            decision_type="skill",
            target_skill_code="person_identity_resolution_atomic",
            reason="定向恢复同名人物裁定",
            arguments=arguments,
        ),
        planner_record_id=None,
        trigger_message_id=None,
    )

    output_status = result.output.get("status")
    if not result.success or output_status == "failed":
        return result

    if output_status not in {"success", "partial"}:
        return result
    passage.workflow_status = output_status
    try:
        db.add(passage)
        db.commit()
    except Exception:
        db.rollback()
        passage.workflow_status = original_status
        raise
    return result


def _resolve_user(db: Session, user_id: int | None) -> User:
    if user_id is not None:
        user = db.get(User, user_id)
    else:
        user = db.query(User).filter(User.role == "admin").order_by(User.id).first()
    if user is None:
        raise RuntimeError("未找到可执行同名裁定的用户。")
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description="定向重跑单篇文章的同名人物裁定。")
    parser.add_argument("--doc-id", type=int, required=True, help="需要恢复的 passages.doc_id")
    parser.add_argument("--user-id", type=int, default=None, help="执行用户 ID，默认取首个管理员")
    parser.add_argument("--new-person-id", type=int, default=None, help="限定新人物节点 ID")
    parser.add_argument(
        "--candidate-person-id",
        type=int,
        default=None,
        help="限定候选人物节点 ID，必须与 --new-person-id 同时提供",
    )
    args = parser.parse_args()
    if (args.new_person_id is None) != (args.candidate_person_id is None):
        parser.error("--new-person-id 与 --candidate-person-id 必须同时提供")

    with SessionLocal() as db:
        passage = db.get(Passage, args.doc_id)
        if passage is None:
            print(f"未找到文章 doc_id={args.doc_id}", file=sys.stderr)
            return 2

        try:
            user = _resolve_user(db, args.user_id)
            result = rerun_identity_resolution(
                db,
                passage,
                user,
                new_person_id=args.new_person_id,
                candidate_person_id=args.candidate_person_id,
            )
        except Exception as exc:
            print(f"同名裁定重跑失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        summary = {
            "doc_id": passage.doc_id,
            "workflow_status": passage.workflow_status,
            "execution_success": result.success,
            "result_status": result.output.get("status"),
            "candidate_pair_count": result.output.get("candidate_pair_count"),
            "review_link_count": result.output.get("review_link_count"),
            "failed_resolution_count": result.output.get("failed_resolution_count"),
            "error_message": result.error_message,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not result.success or result.output.get("status") == "failed":
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
