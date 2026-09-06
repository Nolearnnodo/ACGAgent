from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationReviewDecision,
    AIAnnotationReviewOmission,
    AIAnnotationReviewUpdateRequest,
    EvidenceSpan,
    ExtractionAnnotationLabel,
    ExtractionTaskCreateRequest,
)
from app.services.extraction_ai_annotation_job_service import ExtractionAIAnnotationJobService
from app.services.extraction_ai_annotation_metrics_service import (
    ExtractionAIAnnotationMetricsService,
)
from app.services.extraction_ai_annotation_review_service import (
    AI_ANNOTATION_REVIEW_STANDARD_VERSION,
    ExtractionAIAnnotationReviewError,
    ExtractionAIAnnotationReviewService,
)
from app.services.extraction_ai_annotation_service import ExtractionAIAnnotationService
from app.services.extraction_annotation_service import (
    ExtractionAnnotationService,
    validate_evidence_granularity,
)


@pytest.fixture()
def review_db() -> tuple[Session, User, User, ExtractionAIAnnotationJob]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    db = local_session()
    owner = User(id=1, email="review-owner@example.com", password_hash="x", role="user")
    stranger = User(id=2, email="review-stranger@example.com", password_hash="x", role="user")
    passage = Passage(
        title="李白小传",
        context="李白字太白，少有逸才。",
        source_type="manual_input",
        created_by=owner.id,
        workflow_status="success",
    )
    db.add_all([owner, stranger, passage])
    db.commit()
    db.refresh(passage)
    task = ExtractionAnnotationService(db).create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        owner,
    )
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task.id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    job = ExtractionAIAnnotationJob(
        task_id=task.id,
        passage_id=passage.doc_id,
        requested_by=owner.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    yield db, owner, stranger, job
    db.close()
    engine.dispose()


def _all_correct(review) -> list[AIAnnotationReviewDecision]:
    return [
        AIAnnotationReviewDecision(item_id=item.id, verdict="correct")
        for item in review.items
    ]


def test_review_returns_complete_standard_and_generated_items(review_db):
    db, owner, _stranger, job = review_db

    review = ExtractionAIAnnotationReviewService(db).get_review(job.id, owner)

    assert review.id is None
    assert review.generation_job_id == job.id
    assert review.standard_version == AI_ANNOTATION_REVIEW_STANDARD_VERSION
    assert len(review.standards) == 18
    assert {standard.id for standard in review.standards} == {
        f"S{index:02d}" for index in range(1, 19)
    }
    assert any(item.path.endswith("mentions.0") for item in review.items)
    assert any(item.id == "coverage:persons" for item in review.items)
    assert review.summary.unreviewed_count == len(review.items)


def test_review_resolves_repair_job_to_immutable_generation(review_db):
    db, owner, _stranger, generation = review_db
    repair = ExtractionAIAnnotationJob(
        task_id=generation.task_id,
        passage_id=generation.passage_id,
        requested_by=owner.id,
        operation="repair",
        source_job_id=generation.id,
        status="success",
        provider="mock",
        model="mock",
        result_json=generation.result_json,
    )
    db.add(repair)
    db.commit()
    db.refresh(repair)

    review = ExtractionAIAnnotationReviewService(db).get_review(repair.id, owner)

    assert review.generation_job_id == generation.id


def test_review_draft_persists_reasons_omissions_and_accuracy(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    decisions = _all_correct(review)
    decisions[0] = AIAnnotationReviewDecision(
        item_id=decisions[0].item_id,
        verdict="incorrect",
        reason="材料体裁无直接依据。",
    )
    omission = AIAnnotationReviewOmission(
        id="omission-1",
        category="人物属性",
        target_path="persons.0.courtesy_name",
        description="漏标字太白",
        correction="courtesy_name 补为太白",
        evidence=[
            EvidenceSpan(
                id="omission-evidence-1",
                quote="太白",
                start=3,
                end=5,
                start_utf16=3,
                end_utf16=5,
            )
        ],
    )

    saved = service.save_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions, omissions=[omission]),
        owner,
    )

    assert saved.id is not None
    assert saved.summary.incorrect_count == 1
    assert saved.summary.omission_count == 1
    assert saved.summary.correct_count == len(saved.items) - 1
    assert saved.summary.accuracy == round((len(saved.items) - 1) / (len(saved.items) + 1), 4)
    row = db.query(ExtractionAIAnnotationReview).one()
    assert row.accuracy_basis_points == round(saved.summary.accuracy * 10_000)
    listed = ExtractionAIAnnotationJobService(db).get_job(job.id, owner)
    assert listed.review_status == "draft"
    assert listed.review_accuracy == saved.summary.accuracy


def test_review_submission_requires_every_decision_reason_and_complete_omission(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)

    with pytest.raises(ExtractionAIAnnotationReviewError, match="未判断"):
        service.submit_review(job.id, AIAnnotationReviewUpdateRequest(), owner)

    decisions = _all_correct(review)
    decisions[0] = AIAnnotationReviewDecision(
        item_id=decisions[0].item_id,
        verdict="incorrect",
    )
    with pytest.raises(ExtractionAIAnnotationReviewError, match="错误项"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(decisions=decisions),
            owner,
        )

    decisions[0].reason = "原文依据不足"
    with pytest.raises(ExtractionAIAnnotationReviewError, match="目标字段"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(
                decisions=decisions,
                omissions=[AIAnnotationReviewOmission(id="omission-incomplete")],
            ),
            owner,
        )

    submitted = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert submitted.status == "submitted"
    assert submitted.submitted_at is not None
    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=0)
    assert metrics.summary.human_reviewed_count == 1
    assert metrics.summary.average_human_accuracy == submitted.summary.accuracy
    assert metrics.records[0].human_review_incorrect_count == 1
    assert metrics.records[0].human_review_omission_count == 0
    with pytest.raises(ExtractionAIAnnotationReviewError, match="不能继续修改"):
        service.save_review(
            job.id,
            AIAnnotationReviewUpdateRequest(decisions=decisions),
            owner,
        )


def test_review_rejects_broad_omission_evidence(review_db):
    db, owner, _stranger, job = review_db
    broad_quote = "李白字太白，少有逸才。"

    with pytest.raises(ExtractionAIAnnotationReviewError, match="证据粒度过大"):
        ExtractionAIAnnotationReviewService(db).save_review(
            job.id,
            AIAnnotationReviewUpdateRequest(
                omissions=[
                    AIAnnotationReviewOmission(
                        id="broad-omission",
                        category="人物属性",
                        evidence=[
                            EvidenceSpan(
                                id="broad-omission-evidence",
                                quote=broad_quote,
                                start=0,
                                end=len(broad_quote),
                                start_utf16=0,
                                end_utf16=len(broad_quote),
                            )
                        ],
                    )
                ],
            ),
            owner,
        )


def test_review_is_private_to_job_owner(review_db):
    db, _owner, stranger, job = review_db

    with pytest.raises(ExtractionAIAnnotationReviewError) as exc_info:
        ExtractionAIAnnotationReviewService(db).get_review(job.id, stranger)

    assert exc_info.value.status_code == 404


def test_entity_and_fact_sentence_spans_fail_granularity_check(review_db):
    db, _owner, _stranger, job = review_db
    result = json.loads(job.result_json or "{}")
    label = result["document"]["label"]
    broad_quote = "李白字太白，少有逸才。"
    broad_evidence = {
        "id": "wide-person",
        "source": "context",
        "quote": broad_quote,
        "start": 0,
        "end": len(broad_quote),
        "start_utf16": 0,
        "end_utf16": len(broad_quote),
    }
    label["persons"][0]["mentions"] = [
        broad_evidence,
    ]
    label["unresolved_items"] = [
        {
            "key": "wide-fact",
            "category": "范围测试",
            "note": "完整句子不应作为事实证据",
            "evidence": [{**broad_evidence, "id": "wide-fact-evidence"}],
        }
    ]
    issues = validate_evidence_granularity(ExtractionAnnotationLabel.model_validate(label))

    assert any(issue["code"] == "entity_evidence_too_broad" for issue in issues)
    assert any(issue["code"] == "fact_evidence_too_broad" for issue in issues)
