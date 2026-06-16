from types import SimpleNamespace

import pytest

from app.agents.models import StepExecutionResult
from scripts.rerun_identity_resolution import rerun_identity_resolution


class _Session:
    def __init__(self, fail_commit: bool = False):
        self.fail_commit = fail_commit
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commit_count += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.rollback_count += 1


class _Executor:
    def __init__(self, result: StepExecutionResult):
        self.result = result
        self.context = None
        self.decision = None

    def execute(
        self,
        db,
        context,
        planner_decision,
        planner_record_id,
        trigger_message_id,
    ):
        self.context = context
        self.decision = planner_decision
        return self.result


def _passage(status: str = "partial"):
    return SimpleNamespace(
        doc_id=51,
        title="韦承庆墓志",
        context="墓志原文",
        source_type="upload",
        file_name="51.txt",
        workflow_status=status,
    )


def _user():
    return SimpleNamespace(id=1, role="admin", email="admin@example.com")


def test_rerun_identity_resolution_marks_passage_success():
    db = _Session()
    passage = _passage()
    executor = _Executor(
        StepExecutionResult(
            step_no=1,
            skill_code="person_identity_resolution_atomic",
            success=True,
            output={"status": "success", "review_link_count": 1},
        )
    )

    result = rerun_identity_resolution(
        db=db,
        passage=passage,
        user=_user(),
        executor=executor,
        new_person_id=51015,
        candidate_person_id=22030,
    )

    assert result.success is True
    assert passage.workflow_status == "success"
    assert db.commit_count == 1
    assert executor.context.metadata["trigger_type"] == "identity_resolution_rerun"
    assert executor.context.metadata["passage_id"] == 51
    assert executor.decision.target_skill_code == "person_identity_resolution_atomic"
    assert executor.decision.arguments["new_person_id"] == 51015
    assert executor.decision.arguments["candidate_person_id"] == 22030


def test_rerun_identity_resolution_preserves_partial_status():
    db = _Session()
    passage = _passage()
    executor = _Executor(
        StepExecutionResult(
            step_no=1,
            skill_code="person_identity_resolution_atomic",
            success=True,
            output={"status": "partial", "failed_resolution_count": 1},
        )
    )

    rerun_identity_resolution(
        db=db,
        passage=passage,
        user=_user(),
        executor=executor,
    )

    assert passage.workflow_status == "partial"
    assert db.commit_count == 1


def test_rerun_identity_resolution_does_not_overwrite_status_on_failure():
    db = _Session()
    passage = _passage()
    executor = _Executor(
        StepExecutionResult(
            step_no=1,
            skill_code="person_identity_resolution_atomic",
            success=False,
            output={"status": "failed"},
            error_message="LLM unavailable",
        )
    )

    result = rerun_identity_resolution(
        db=db,
        passage=passage,
        user=_user(),
        executor=executor,
    )

    assert result.success is False
    assert passage.workflow_status == "partial"
    assert db.commit_count == 0


def test_rerun_identity_resolution_restores_status_when_commit_fails():
    db = _Session(fail_commit=True)
    passage = _passage()
    executor = _Executor(
        StepExecutionResult(
            step_no=1,
            skill_code="person_identity_resolution_atomic",
            success=True,
            output={"status": "success"},
        )
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        rerun_identity_resolution(
            db=db,
            passage=passage,
            user=_user(),
            executor=executor,
        )

    assert passage.workflow_status == "partial"
    assert db.rollback_count == 1
