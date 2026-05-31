from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import base  # noqa: F401
from app.db.base_class import Base
from app.models.execution import ExecutionRun
from app.models.observability import ExecutionTraceSummary, LLMCallLog
from app.models.passage import Passage
from app.models.user import User
from app.services.passage_service import PassageService


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_user_and_passage(db):
    user = User(email="tester@example.com", password_hash="hash", role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)

    passage = Passage(
        title="墓志一",
        context="context",
        source_type="upload",
        created_by=user.id,
        workflow_status="success",
    )
    db.add(passage)
    db.commit()
    db.refresh(passage)
    return user, passage


def test_passage_token_usage_resets_to_latest_rerun_even_without_new_llm_calls():
    Session = _make_session()

    with Session() as db:
        user, passage = _seed_user_and_passage(db)

        first_run = ExecutionRun(trigger_type="passage_upload", status="success", passage_id=passage.doc_id)
        db.add(first_run)
        db.commit()
        db.refresh(first_run)

        db.add(
            ExecutionTraceSummary(
                execution_run_id=first_run.id,
                llm_call_count=1,
                total_tokens=120,
                prompt_tokens=100,
                completion_tokens=20,
                prompt_cache_hit_tokens=60,
                prompt_cache_miss_tokens=40,
                estimated_total_cost=0.12,
            )
        )
        db.add(
            LLMCallLog(
                execution_run_id=first_run.id,
                passage_id=passage.doc_id,
                skill_code="probe_atomic",
                provider="fake",
                model="fake-model",
                total_tokens=120,
                prompt_tokens=100,
                completion_tokens=20,
                prompt_cache_hit_tokens=60,
                prompt_cache_miss_tokens=40,
            )
        )
        db.commit()

        rerun = ExecutionRun(trigger_type="passage_rerun", status="running", passage_id=passage.doc_id)
        db.add(rerun)
        db.commit()
        db.refresh(rerun)

        service = PassageService(db)
        execution_run_id, summary, calls = service.get_latest_passage_trace(passage.doc_id)
        overview = service.get_passage_usage_overview_rows()

        assert execution_run_id == rerun.id
        assert summary is None
        assert calls == []

        assert len(overview) == 1
        assert overview[0].doc_id == passage.doc_id
        assert overview[0].llm_call_count == 0
        assert overview[0].total_tokens == 0


def test_passage_usage_overview_only_counts_latest_run_summary():
    Session = _make_session()

    with Session() as db:
        user, passage = _seed_user_and_passage(db)

        first_run = ExecutionRun(trigger_type="passage_upload", status="success", passage_id=passage.doc_id)
        second_run = ExecutionRun(trigger_type="passage_rerun", status="success", passage_id=passage.doc_id)
        db.add_all([first_run, second_run])
        db.commit()
        db.refresh(first_run)
        db.refresh(second_run)

        db.add_all(
            [
                ExecutionTraceSummary(
                    execution_run_id=first_run.id,
                    llm_call_count=2,
                    total_tokens=120,
                    prompt_tokens=100,
                    completion_tokens=20,
                    prompt_cache_hit_tokens=60,
                    prompt_cache_miss_tokens=40,
                    estimated_total_cost=0.12,
                ),
                ExecutionTraceSummary(
                    execution_run_id=second_run.id,
                    llm_call_count=1,
                    total_tokens=30,
                    prompt_tokens=24,
                    completion_tokens=6,
                    prompt_cache_hit_tokens=12,
                    prompt_cache_miss_tokens=12,
                    estimated_total_cost=0.03,
                ),
            ]
        )
        db.commit()

        service = PassageService(db)
        execution_run_id, summary, calls = service.get_latest_passage_trace(passage.doc_id)
        overview = service.get_passage_usage_overview_rows()

        assert execution_run_id == second_run.id
        assert summary is not None
        assert summary.total_tokens == 30
        assert calls == []

        assert len(overview) == 1
        assert overview[0].llm_call_count == 1
        assert overview[0].total_tokens == 30
