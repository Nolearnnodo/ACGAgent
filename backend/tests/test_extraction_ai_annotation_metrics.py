from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
)
from app.models.extraction_annotation import ExtractionAnnotationSubmission
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationTokenUsage,
    AIAnnotationValidationIssue,
    ExtractionTaskCreateRequest,
)
from app.services.extraction_ai_annotation_metrics_service import (
    ExtractionAIAnnotationMetricsService,
)
from app.services import extraction_ai_annotation_job_service as job_module
from app.services.extraction_ai_annotation_job_service import (
    ExtractionAIAnnotationJobService,
    _run_job_background,
)
from app.services.extraction_ai_annotation_service import ExtractionAIAnnotationService
from app.services.extraction_annotation_service import ExtractionAnnotationService


@pytest.fixture()
def metrics_db() -> tuple[Session, int, User]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    db = local_session()
    admin = User(id=1, email="metrics-admin@example.com", password_hash="x", role="admin")
    annotator = User(id=2, email="metrics-user@example.com", password_hash="x", role="user")
    passage = Passage(
        title="AI 统计测试篇目",
        context="李白字太白，少有逸才。",
        source_type="manual_input",
        created_by=admin.id,
        workflow_status="success",
    )
    db.add_all([admin, annotator, passage])
    db.commit()
    db.refresh(passage)
    task = ExtractionAnnotationService(db).create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    yield db, task.id, annotator
    db.close()
    engine.dispose()


def test_ai_metrics_track_click_tokens_errors_repairs_and_final_submission(metrics_db):
    db, task_id, annotator = metrics_db
    task_service = ExtractionAnnotationService(db)
    claimed = task_service.claim_task(task_id, annotator)
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None

    now = datetime.now(timezone.utc)
    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=generated.passage_id,
        requested_by=annotator.id,
        operation="generate",
        status="success",
        provider="deepseek",
        model="deepseek-chat",
        config_json="{}",
        prompt_json="{}",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
        client_started_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=10) + timedelta(milliseconds=120),
        started_at=now - timedelta(minutes=9),
        finished_at=now - timedelta(minutes=8),
        prompt_tokens=100,
        completion_tokens=30,
        # 兼容历史 Provider 没有回传 total_tokens 的记录。
        total_tokens=0,
        prompt_cache_hit_tokens=40,
        prompt_cache_miss_tokens=60,
        cache_metrics_supported=True,
        llm_call_count=8,
        first_round_validation_status="invalid_rules",
        first_round_error_count=1,
        first_round_errors_json=json.dumps(
            [
                {
                    "path": "persons.0.mentions.0",
                    "message": "测试用首轮证据错误",
                    "code": "evidence_quote_mismatch",
                }
            ],
            ensure_ascii=False,
        ),
    )
    db.add(root)
    db.commit()
    db.refresh(root)
    repair = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=generated.passage_id,
        requested_by=annotator.id,
        operation="repair",
        source_job_id=root.id,
        status="success",
        provider="deepseek",
        model="deepseek-chat",
        config_json="{}",
        prompt_json="{}",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
        client_started_at=now - timedelta(minutes=7),
        started_at=now - timedelta(minutes=7),
        finished_at=now - timedelta(minutes=6),
        prompt_tokens=50,
        completion_tokens=20,
        total_tokens=70,
        llm_call_count=2,
    )
    db.add(repair)
    db.commit()
    db.refresh(repair)

    review = ExtractionAIAnnotationReview(
        generation_job_id=root.id,
        task_id=task_id,
        passage_id=generated.passage_id,
        reviewer_id=annotator.id,
        status="submitted",
        standard_version="2026-09-07-v1",
        started_at=now - timedelta(minutes=7),
        submitted_at=now - timedelta(minutes=6),
        created_at=now - timedelta(minutes=7),
        updated_at=now - timedelta(minutes=6),
    )
    db.add(review)
    db.commit()

    imported = task_service.import_draft(
        task_id,
        annotator,
        claimed.submission.revision,
        json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False),
        source_ai_job_id=repair.id,
    )
    first_imported_at = db.get(
        ExtractionAnnotationSubmission,
        imported.submission.id,
    ).ai_imported_at
    reimported = task_service.import_draft(
        task_id,
        annotator,
        imported.submission.revision,
        json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False),
        source_ai_job_id=repair.id,
    )
    submission = db.get(ExtractionAnnotationSubmission, reimported.submission.id)
    assert submission is not None
    assert submission.ai_imported_at == first_imported_at
    submission.ai_imported_at = now - timedelta(minutes=5)
    db.add(submission)
    db.commit()

    final_label = reimported.submission.label.model_copy(deep=True)
    final_label.passage.note = "人工核对后补充"
    final_label.step_reviews = {
        "material": True,
        "persons": True,
        "relations": True,
        "checks": True,
    }
    submitted = task_service.submit(
        task_id,
        annotator,
        reimported.submission.revision,
        final_label,
    )

    submission = db.get(ExtractionAnnotationSubmission, submitted.submission.id)
    db.refresh(root)
    assert submission is not None
    assert submission.source_ai_job_id == repair.id
    assert root.submitted_at is not None
    assert root.final_difference_count == 1
    assert json.loads(root.final_differences_json) == {"passage": 1}

    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=30)
    assert metrics.summary.session_count == 1
    assert metrics.summary.submitted_count == 1
    assert metrics.summary.total_tokens == 200
    assert metrics.summary.total_prompt_tokens == 150
    assert metrics.summary.total_completion_tokens == 50
    assert metrics.summary.total_llm_call_count == 10
    assert metrics.summary.total_repair_rounds == 1
    assert metrics.summary.total_first_round_errors == 1
    assert metrics.summary.total_final_differences == 1
    assert metrics.validation_errors[0].code == "evidence_quote_mismatch"
    assert metrics.final_differences[0].category == "passage"

    record = metrics.records[0]
    assert record.session_id == root.id
    assert record.submission_id == submission.id
    assert record.end_to_end_ms is not None and record.end_to_end_ms >= 9 * 60 * 1000
    assert record.turnaround_elapsed_ms == record.end_to_end_ms
    assert record.queue_wait_ms == 59_880
    assert record.first_round_elapsed_ms == 60 * 1000
    assert record.human_processing_elapsed_ms is not None
    assert record.human_processing_elapsed_ms >= 7 * 60 * 1000
    assert record.human_review_elapsed_ms == 60 * 1000
    assert record.post_import_edit_elapsed_ms is not None
    assert record.post_import_edit_elapsed_ms >= 5 * 60 * 1000
    assert record.human_processing_started_at is not None
    assert record.human_review_started_at is not None
    assert record.ai_imported_at is not None
    assert record.repair_rounds == 1
    assert record.token_usage.total_tokens == 200
    assert metrics.summary.human_processing_count == 1
    assert metrics.summary.average_human_processing_ms == record.human_processing_elapsed_ms
    assert metrics.summary.average_human_review_ms == 60 * 1000
    assert metrics.summary.post_import_edit_count == 1
    assert metrics.summary.average_post_import_edit_ms == record.post_import_edit_elapsed_ms


def test_background_job_persists_usage_and_first_round_snapshot(metrics_db, monkeypatch):
    db, task_id, annotator = metrics_db
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    generated.validation_status = "invalid_rules"
    generated.validation_issues = [
        AIAnnotationValidationIssue(
            path="persons.0",
            message="首轮测试错误",
            code="test_first_round_error",
        )
    ]
    generated.fragment_warnings = ["一个待复核分片"]
    generated.truncated_fragments = ["人物分片"]
    generated.token_usage = AIAnnotationTokenUsage(
        prompt_tokens=321,
        completion_tokens=123,
        total_tokens=444,
        prompt_cache_hit_tokens=111,
        prompt_cache_miss_tokens=210,
        cache_metrics_supported=True,
        llm_call_count=9,
    )

    clicked_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)
    local_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(job_module, "SessionLocal", local_session)
    monkeypatch.setattr(
        ExtractionAIAnnotationService,
        "generate",
        lambda self, payload: generated,
    )
    payload = AIAnnotationGenerateRequest(
        task_id=task_id,
        config=AIAnnotationConfigRequest(provider="mock"),
        client_started_at=clicked_at,
    )
    queued = ExtractionAIAnnotationJobService(db).enqueue(payload, annotator)
    _run_job_background(queued.id, payload)

    saved = db.get(ExtractionAIAnnotationJob, queued.id)
    assert saved is not None
    assert saved.client_started_at is not None
    assert abs((saved.client_started_at.replace(tzinfo=timezone.utc) - clicked_at).total_seconds()) < 0.01
    assert saved.prompt_tokens == 321
    assert saved.completion_tokens == 123
    assert saved.total_tokens == 444
    assert saved.prompt_cache_hit_tokens == 111
    assert saved.llm_call_count == 9
    assert saved.first_round_validation_status == "invalid_rules"
    assert saved.first_round_error_count == 1
    assert json.loads(saved.first_round_errors_json)[0]["code"] == "test_first_round_error"
    assert saved.first_round_warning_count == 1
    assert saved.first_round_truncated_count == 1


def test_ai_metrics_exclude_legacy_coverage_and_report_omissions_separately(metrics_db):
    db, task_id, annotator = metrics_db
    passage_id = db.query(Passage.doc_id).scalar()
    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=passage_id,
        requested_by=annotator.id,
        operation="generate",
        status="success",
        result_json="{}",
        first_round_validation_status="valid",
    )
    db.add(root)
    db.flush()
    review = ExtractionAIAnnotationReview(
        generation_job_id=root.id,
        task_id=task_id,
        passage_id=passage_id,
        reviewer_id=annotator.id,
        status="submitted",
        standard_version="legacy-with-coverage",
        decisions_json=json.dumps(
            [
                {"item_id": "coverage:persons", "verdict": "correct"},
                {"item_id": "coverage:events", "verdict": "incorrect"},
                {"item_id": "passage:source_type", "verdict": "correct"},
                {"item_id": "person:p1:name", "verdict": "incorrect"},
            ]
        ),
        omissions_json=json.dumps(
            [
                {"category": "人物缺漏", "target_path": "persons", "description": "遗漏李白之子"},
                {"category": "关系缺漏", "target_path": "relations", "description": "遗漏父子关系", "correction": "李白与其子的父子关系"},
            ],
            ensure_ascii=False,
        ),
        # 老数据的预存口径把覆盖检查及缺漏都放进了正确率。
        correct_count=2,
        incorrect_count=2,
        omission_count=2,
        accuracy_basis_points=3333,
    )
    db.add(review)
    db.commit()

    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=0)
    record = metrics.records[0]
    assert record.human_review_correct_count == 1
    assert record.human_review_incorrect_count == 1
    assert record.human_review_accuracy == 0.5
    assert record.human_review_error_categories == {"人物": 1}
    assert record.human_review_omission_count == 2
    assert record.human_review_omission_categories == {"人物缺漏": 1, "关系缺漏": 1}
    assert [item.description for item in record.human_review_omissions] == [
        "遗漏李白之子", "遗漏父子关系"
    ]
    assert record.human_review_omissions[1].correction == "李白与其子的父子关系"
    assert metrics.summary.average_human_accuracy == 0.5
    assert metrics.summary.human_scored_count == 1
    assert metrics.summary.total_human_correct == 1
    assert metrics.summary.total_human_incorrect == 1
    assert metrics.summary.total_human_omissions == 2
    assert metrics.summary.human_omission_categories == {"人物缺漏": 1, "关系缺漏": 1}


def test_ai_metrics_do_not_guess_accuracy_when_legacy_decisions_are_missing(metrics_db):
    db, task_id, annotator = metrics_db
    passage_id = db.query(Passage.doc_id).scalar()
    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=passage_id,
        requested_by=annotator.id,
        operation="generate",
        status="success",
        result_json="{}",
    )
    db.add(root)
    db.flush()
    db.add(
        ExtractionAIAnnotationReview(
            generation_job_id=root.id,
            task_id=task_id,
            passage_id=passage_id,
            reviewer_id=annotator.id,
            status="submitted",
            standard_version="legacy-summary-only",
            decisions_json="[]",
            omissions_json="[]",
            correct_count=4,
            incorrect_count=1,
            omission_count=2,
            accuracy_basis_points=6667,
        )
    )
    db.commit()

    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=0)
    record = metrics.records[0]
    assert record.human_review_accuracy is None
    assert record.human_review_correct_count == 0
    assert record.human_review_incorrect_count == 0
    assert record.human_review_omission_count == 2
    assert record.human_review_omission_categories == {"历史缺漏（未分类）": 2}
    assert record.human_review_omissions[0].count == 2
    assert metrics.summary.human_scored_count == 0
    assert metrics.summary.human_omission_categories == {"历史缺漏（未分类）": 2}
