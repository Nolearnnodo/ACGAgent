from __future__ import annotations

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
    ExtractionAIAnnotationSavedResult,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationReviewDecision,
    AIAnnotationReviewOmission,
    AIAnnotationReviewUpdateRequest,
    EventLocation,
    EventTime,
    ExcludedMention,
    EvidenceSpan,
    ExtractionAnnotationLabel,
    ExtractionTaskCreateRequest,
    HistoricalEventAnnotation,
    LifeEventAnnotation,
    PassageAnnotation,
    PersonAnnotation,
    PersonRelationAnnotation,
)
from app.services.extraction_ai_annotation_job_service import ExtractionAIAnnotationJobService
from app.services.extraction_ai_annotation_metrics_service import (
    ExtractionAIAnnotationMetricsService,
)
from app.services.extraction_ai_annotation_review_apply import apply_ai_review_to_label
from app.services.extraction_ai_annotation_review_service import (
    AI_ANNOTATION_REVIEW_STANDARD_VERSION,
    ExtractionAIAnnotationReviewError,
    ExtractionAIAnnotationReviewService,
    build_ai_annotation_review_items,
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
    assert any(item.id == "passage:source_type" for item in review.items)
    assert any(item.id.endswith(":courtesy_name") for item in review.items)
    assert review.summary.unreviewed_count == sum(
        not item.id.startswith("coverage:") for item in review.items
    )


def test_start_review_records_server_start_once(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)

    started = service.start_review(job.id, owner)
    assert started.id is not None
    assert started.started_at is not None
    assert started.created_at is not None
    assert started.summary.unreviewed_count == sum(
        not item.id.startswith("coverage:") for item in started.items
    )

    row = db.query(ExtractionAIAnnotationReview).one()
    first_started_at = row.started_at
    repeated = service.start_review(job.id, owner)

    assert repeated.id == started.id
    assert repeated.started_at == first_started_at
    assert db.query(ExtractionAIAnnotationReview).count() == 1


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
        correction="一般历史文献",
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
    scored_count = sum(not item.id.startswith("coverage:") for item in saved.items)
    assert saved.summary.correct_count == scored_count - 1
    assert saved.summary.accuracy == round((scored_count - 1) / scored_count, 4)
    row = db.query(ExtractionAIAnnotationReview).one()
    assert row.accuracy_basis_points == round(saved.summary.accuracy * 10_000)
    listed = ExtractionAIAnnotationJobService(db).get_job(job.id, owner)
    assert listed.review_status == "draft"
    assert listed.review_accuracy == saved.summary.accuracy


def test_review_persists_selection_offsets_for_incorrect_mention(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    mention = next(item for item in review.items if item.path.endswith("mentions.0"))
    decisions = _all_correct(review)
    for index, decision in enumerate(decisions):
        if decision.item_id == mention.id:
            decisions[index] = AIAnnotationReviewDecision(
                item_id=mention.id,
                verdict="incorrect",
                correction_evidence=[
                    EvidenceSpan(
                        id="corrected-mention",
                        quote="李白",
                        start=0,
                        end=2,
                        start_utf16=0,
                        end_utf16=2,
                    )
                ],
            )
            break

    saved = service.save_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    stored = next(item for item in saved.items if item.id == mention.id)

    assert stored.verdict == "incorrect"
    assert stored.correction == "李白"
    assert stored.correction_evidence[0].start == 0
    assert stored.correction_evidence[0].end == 2
    assert stored.correction_evidence[0].quote == "李白"


def test_review_submission_requires_every_decision_correction_and_complete_omission(
    review_db,
    monkeypatch,
):
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
    with pytest.raises(ExtractionAIAnnotationReviewError, match="正确值"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(decisions=decisions),
            owner,
        )

    decisions[0].correction = "一般历史文献"
    with pytest.raises(ExtractionAIAnnotationReviewError, match="目标字段"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(
                decisions=decisions,
                omissions=[AIAnnotationReviewOmission(id="omission-incomplete")],
            ),
            owner,
        )

    original_commit = db.commit
    commit_count = 0

    def counted_commit():
        nonlocal commit_count
        commit_count += 1
        return original_commit()

    monkeypatch.setattr(db, "commit", counted_commit)
    submitted = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert commit_count == 1
    assert submitted.status == "submitted"
    assert submitted.submitted_at is not None
    db.expire_all()
    stored_review = db.query(ExtractionAIAnnotationReview).one()
    assert stored_review.status == "submitted"
    assert stored_review.submitted_at is not None
    listed_after_submit = ExtractionAIAnnotationJobService(db).get_job(job.id, owner)
    assert listed_after_submit.review_status == "submitted"
    assert listed_after_submit.review_submitted_at == stored_review.submitted_at
    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=0)
    assert metrics.summary.human_reviewed_count == 1
    assert metrics.summary.average_human_accuracy == submitted.summary.accuracy
    assert metrics.records[0].human_review_incorrect_count == 1
    assert metrics.records[0].human_review_omission_count == 0
    assert metrics.records[0].human_review_error_categories == {"材料": 1}
    assert [
        item.model_dump() for item in metrics.records[0].human_review_error_types
    ] == [
        {
            "category": "材料",
            "field_label": "文献类型",
            "count": 1,
        }
    ]
    first_submitted_at = submitted.submitted_at
    decisions[0].correction = "暂不能判断"
    resubmitted = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert resubmitted.status == "submitted"
    assert resubmitted.editable is True
    assert resubmitted.submitted_at is not None
    assert resubmitted.submitted_at >= first_submitted_at
    assert resubmitted.ground_truth_document is not None
    assert resubmitted.ground_truth_document.label.passage.source_type == "uncertain"

    draft = service.save_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert draft.status == "draft"
    assert draft.editable is True
    assert draft.submitted_at == resubmitted.submitted_at

    submitted_again = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert submitted_again.status == "submitted"
    assert submitted_again.editable is True


def test_review_new_event_fact_requires_complete_card_and_carries_it_into_ground_truth(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    event_check = next(
        item
        for item in review.items
        if ":event-check:" in item.id and item.ai_value != "已检查且有事实"
    )
    decisions = _all_correct(review)
    decision_index = next(
        index for index, decision in enumerate(decisions) if decision.item_id == event_check.id
    )
    decisions[decision_index] = AIAnnotationReviewDecision(
        item_id=event_check.id,
        verdict="incorrect",
        correction="已检查且有事实",
    )

    with pytest.raises(ExtractionAIAnnotationReviewError, match="原文证据"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(decisions=decisions),
            owner,
        )

    evidence = EvidenceSpan(
        id="review-new-event-evidence",
        quote="李白",
        start=0,
        end=2,
        start_utf16=0,
        end_utf16=2,
    )
    decisions[decision_index].correction_evidence = [evidence]
    with pytest.raises(ExtractionAIAnnotationReviewError, match="完整事件卡片"):
        service.submit_review(
            job.id,
            AIAnnotationReviewUpdateRequest(decisions=decisions),
            owner,
        )

    person_key, event_type = event_check.id.removeprefix("person:").split(":event-check:", 1)
    event_time = EventTime(
        state="not_applicable" if event_type == "籍贯" else "present",
        raw="" if event_type == "籍贯" else "开元二十年",
        era="" if event_type == "籍贯" else "开元",
        era_year=None if event_type == "籍贯" else 20,
    )
    decisions[decision_index].correction_data = {
        "key": "review-complete-event",
        "event_type": event_type,
        "state": "confirmed",
        "time": event_time.model_dump(mode="python"),
        "location": EventLocation(
            state="present",
            raw="长安",
            xian="长安",
        ).model_dump(mode="python"),
        "official_title": "翰林供奉" if event_type == "任职" else "",
        "note": "人工质检完整补录",
    }
    submitted = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )

    assert submitted.ground_truth_document is not None
    person = next(
        item for item in submitted.ground_truth_document.label.persons if item.key == person_key
    )
    assert person.event_checks[event_type] == "has_fact"
    event = next(item for item in person.life_events if item.event_type == event_type)
    assert event.key == "review-complete-event"
    assert event.state == "confirmed"
    assert event.time == event_time
    assert event.location.raw == "长安"
    assert event.location.xian == "长安"
    assert event.official_title == ("翰林供奉" if event_type == "任职" else "")
    assert event.note == "人工质检完整补录"
    assert event.evidence == [evidence]
    saved_item = next(item for item in submitted.items if item.id == event_check.id)
    assert saved_item.correction_data["key"] == "review-complete-event"


def test_review_locks_only_after_blind_annotation_submission(review_db):
    db, owner, _stranger, job = review_db
    review_service = ExtractionAIAnnotationReviewService(db)
    review = review_service.get_review(job.id, owner)
    decisions = _all_correct(review)
    submitted = review_service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    assert submitted.editable is True
    assert submitted.ground_truth_document is not None

    annotation_service = ExtractionAnnotationService(db)
    claimed = annotation_service.claim_task(job.task_id, owner)
    submitted.ground_truth_document.label.step_reviews = {
        "material": True, "persons": True, "relations": True, "checks": True,
    }
    blind_submission = annotation_service.submit(
        job.task_id,
        owner,
        claimed.submission.revision,
        submitted.ground_truth_document.label,
    )
    assert blind_submission.submission.state == "submitted"

    locked = review_service.get_review(job.id, owner)
    assert locked.status == "submitted"
    assert locked.editable is False
    assert locked.blind_submitted_at is not None
    with pytest.raises(ExtractionAIAnnotationReviewError, match="盲标已提交"):
        review_service.save_review(
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


def test_loading_submitted_review_repairs_missing_submission_time(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    decisions = _all_correct(review)
    service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )
    row = db.query(ExtractionAIAnnotationReview).one()
    row.submitted_at = None
    db.add(row)
    db.commit()

    repaired = service.get_review(job.id, owner)

    assert repaired.status == "submitted"
    assert repaired.submitted_at is not None
    db.expire_all()
    assert db.query(ExtractionAIAnnotationReview).one().submitted_at is not None


def test_latest_success_job_uses_its_own_review_not_an_older_saved_row(review_db):
    db, owner, _stranger, source_job = review_db
    review_service = ExtractionAIAnnotationReviewService(db)
    review = review_service.get_review(source_job.id, owner)
    submitted = review_service.submit_review(
        source_job.id,
        AIAnnotationReviewUpdateRequest(decisions=_all_correct(review)),
        owner,
    )
    saved = ExtractionAIAnnotationSavedResult(
        task_id=source_job.task_id,
        passage_id=source_job.passage_id,
        requested_by=owner.id,
        source_job_id=source_job.id,
        result_json=source_job.result_json or "{}",
    )
    newer_job = ExtractionAIAnnotationJob(
        task_id=source_job.task_id,
        passage_id=source_job.passage_id,
        requested_by=owner.id,
        operation="generate",
        status="success",
        provider="mock",
        model="newer-mock",
        result_json=source_job.result_json,
        created_at=source_job.created_at + timedelta(seconds=1),
        updated_at=source_job.updated_at + timedelta(seconds=1),
    )
    db.add_all([saved, newer_job])
    db.commit()
    newer_job_id = newer_job.id
    source_job_id = source_job.id
    owner_id = owner.id
    submitted_at = submitted.submitted_at

    fresh_db = Session(bind=db.get_bind())
    try:
        fresh_owner = fresh_db.get(User, owner_id)
        assert fresh_owner is not None
        latest = ExtractionAIAnnotationJobService(fresh_db).list_latest_jobs(fresh_owner)[0]

        assert latest.id == newer_job_id
        assert latest.saved_result_source_job_id == newer_job_id
        assert latest.generation_job_id == newer_job_id
        assert latest.review_status == "not_started"
        assert latest.review_submitted_at is None

        older = ExtractionAIAnnotationReviewService(fresh_db).get_review(
            source_job_id,
            fresh_owner,
        )
        assert older.status == "submitted"
        assert older.submitted_at == submitted_at
        assert older.summary.unreviewed_count == 0
        assert all(item.verdict == "correct" for item in older.items)
    finally:
        fresh_db.close()


def test_latest_failed_job_falls_back_to_earlier_submitted_generation(review_db):
    db, owner, _stranger, source_job = review_db
    review_service = ExtractionAIAnnotationReviewService(db)
    review = review_service.get_review(source_job.id, owner)
    review_service.submit_review(
        source_job.id,
        AIAnnotationReviewUpdateRequest(decisions=_all_correct(review)),
        owner,
    )
    saved = ExtractionAIAnnotationSavedResult(
        task_id=source_job.task_id,
        passage_id=source_job.passage_id,
        requested_by=owner.id,
        source_job_id=source_job.id,
        lineage_job_id=source_job.id,
        result_json=source_job.result_json or "{}",
    )
    failed_job = ExtractionAIAnnotationJob(
        task_id=source_job.task_id,
        passage_id=source_job.passage_id,
        requested_by=owner.id,
        operation="generate",
        status="failed",
        provider="mock",
        model="failed-mock",
        error_message="模型没有返回内容",
        created_at=source_job.created_at + timedelta(seconds=2),
        updated_at=source_job.updated_at + timedelta(seconds=2),
    )
    db.add_all([saved, failed_job])
    db.commit()
    failed_job_id = failed_job.id
    source_job_id = source_job.id
    owner_id = owner.id

    fresh_db = Session(bind=db.get_bind())
    try:
        fresh_owner = fresh_db.get(User, owner_id)
        assert fresh_owner is not None
        service = ExtractionAIAnnotationJobService(fresh_db)
        latest = service.list_latest_jobs(fresh_owner)[0]

        assert latest.id == failed_job_id
        assert latest.status == "failed"
        assert latest.error_message == "模型没有返回内容"
        assert latest.saved_result_source_job_id == source_job_id
        assert latest.generation_job_id == source_job_id
        assert latest.review_status == "submitted"

        strict = service.get_job(failed_job_id, fresh_owner)
        assert strict.generation_job_id == failed_job_id
        assert strict.review_status == "not_started"
        assert strict.saved_result is None
        assert strict.saved_result_source_job_id is None
    finally:
        fresh_db.close()


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


def test_review_items_expand_each_fillable_field():
    mention = EvidenceSpan(id="m1", quote="李白", start=0, end=2, start_utf16=0, end_utf16=2)
    event_evidence = EvidenceSpan(
        id="ev-e1",
        quote="开元二十年",
        start=10,
        end=15,
        start_utf16=10,
        end_utf16=15,
    )
    history_evidence = EvidenceSpan(
        id="h-e1",
        quote="安史之乱",
        start=20,
        end=24,
        start_utf16=20,
        end_utf16=24,
    )
    relation_evidence = EvidenceSpan(
        id="r-e1",
        quote="其父",
        start=30,
        end=32,
        start_utf16=30,
        end_utf16=32,
    )
    label = ExtractionAnnotationLabel(
        passage=PassageAnnotation(
            source_type="epitaph",
            is_female=False,
            era="开元",
        ),
        persons=[
            PersonAnnotation(
                key="p1",
                name_surface="李白",
                courtesy_name="太白",
                level=1,
                level_reason="主人公",
                mentions=[mention],
                event_checks={
                    "出生": "not_mentioned",
                    "籍贯": "not_mentioned",
                    "死亡": "not_mentioned",
                    "埋葬": "not_mentioned",
                    "任职": "has_fact",
                },
                life_events=[
                    LifeEventAnnotation(
                        key="ev1",
                        event_type="任职",
                        official_title="翰林供奉",
                        time=EventTime(state="present", raw="开元二十年", era="开元", era_year=20),
                        location=EventLocation(state="present", raw="长安", xian="长安"),
                        evidence=[event_evidence],
                    )
                ],
                historical_events=[
                    HistoricalEventAnnotation(
                        key="h1",
                        event_name="安史之乱",
                        relation_summary="因乱扈从",
                        evidence=[history_evidence],
                    )
                ],
            ),
            PersonAnnotation(
                key="p2",
                name_surface="李客",
                level=2,
                level_reason="墓主之父",
                mentions=[],
            ),
        ],
        person_relations=[
            PersonRelationAnnotation(
                key="r1",
                source_person_key="p1",
                target_person_key="p2",
                relation_category="亲属",
                relation_summary="父子",
                codes=["F"],
                reverse_codes=["S"],
                evidence=[relation_evidence],
            )
        ],
    )

    items = build_ai_annotation_review_items(label)
    by_id = {item.id: item for item in items}

    assert "passage:attributes" not in by_id
    assert "person:p1:identity" not in by_id
    assert by_id["passage:source_type"].ai_value == "墓志铭或塔铭类"
    assert by_id["passage:is_female"].ai_value == "否"
    assert by_id["person:p1:name_surface"].field_label == "原文姓名"
    assert by_id["person:p1:courtesy_name"].ai_value == "太白"
    assert by_id["person:p1:event-check:任职"].ai_value == "已检查且有事实"
    assert by_id["person:p1:event-check:任职"].evidence == [event_evidence]
    assert by_id["person:p1:event-check:出生"].evidence == []
    assert by_id["event:p1:ev1:time.era"].ai_value == "开元"
    assert by_id["event:p1:ev1:location.xian"].ai_value == "长安"
    assert by_id["event:p1:ev1:official_title"].ai_value == "翰林供奉"
    assert by_id["history:p1:h1:event_name"].ai_value == "安史之乱"
    assert by_id["relation:r1:relation_category"].ai_value == "亲属"
    assert by_id["relation:r1:relation_summary"].ai_value == "父子"
    assert "relation:r1:codes" not in by_id
    assert by_id["person:p1:mention:0"].ai_value == "“李白”　第 0–1 字（2 字）"
    assert by_id["person:p1:mention:0"].value_kind == "evidence"
    assert "不必填写" in by_id["person:p1:mention:0"].correction_hint
    assert by_id["person:p1:name_surface"].group_key == "person:p1"
    assert by_id["event:p1:ev1:time.raw"].section_title.startswith("任职")
    assert {item.group_key for item in items} >= {
        "passage",
        "coverage",
        "person:p1",
        "person:p2",
        "relations",
    }


def test_apply_review_writes_corrections_and_person_omissions_into_label():
    mention = EvidenceSpan(id="m1", quote="李白", start=0, end=2, start_utf16=0, end_utf16=2)
    label = ExtractionAnnotationLabel(
        passage=PassageAnnotation(source_type="epitaph", era="开元"),
        persons=[
            PersonAnnotation(
                key="p1",
                name_surface="李白",
                courtesy_name="太白",
                level=1,
                mentions=[mention],
            )
        ],
    )
    items = build_ai_annotation_review_items(label)
    by_id = {item.id: item for item in items}
    by_id["passage:source_type"].verdict = "incorrect"
    by_id["passage:source_type"].correction = "一般历史文献"
    by_id["person:p1:courtesy_name"].verdict = "incorrect"
    by_id["person:p1:courtesy_name"].correction = "（空）"
    by_id["person:p1:mention:0"].verdict = "incorrect"
    by_id["person:p1:mention:0"].correction_evidence = [
        EvidenceSpan(id="m1-fix", quote="李", start=0, end=1, start_utf16=0, end_utf16=1)
    ]
    omissions = [
        AIAnnotationReviewOmission(
            id="miss-1",
            category="人物",
            target_path="persons",
            description="漏建李客",
            correction="李客",
            evidence=[
                EvidenceSpan(id="miss-e", quote="李客", start=10, end=12, start_utf16=10, end_utf16=12)
            ],
        )
    ]
    context = "李白字太白，其父李客。"
    patched = apply_ai_review_to_label(label, items, omissions, context)

    assert patched.passage.source_type == "history"
    assert patched.persons[0].courtesy_name == ""
    assert patched.persons[0].mentions[0].quote == "李"
    assert patched.persons[0].mentions[0].end == 1
    assert any(person.name_surface == "李客" for person in patched.persons)


def test_apply_review_can_remove_an_excluded_mention():
    evidence = EvidenceSpan(
        id="x1-e1",
        quote="诸公",
        start=0,
        end=2,
        start_utf16=0,
        end_utf16=2,
    )
    label = ExtractionAnnotationLabel(
        passage=PassageAnnotation(source_type="epitaph"),
        excluded_mentions=[
            ExcludedMention(key="x1", reason="群体称谓", evidence=[evidence])
        ],
    )
    items = build_ai_annotation_review_items(label)
    excluded_items = [
        item for item in items if item.path.startswith("excluded_mentions.")
    ]
    assert excluded_items
    for item in excluded_items:
        item.verdict = "incorrect"
        item.remove_item = True

    patched = apply_ai_review_to_label(label, items, [], "诸公")

    assert patched.excluded_mentions == []


def test_apply_review_can_remove_person_event_relation_and_single_evidence():
    context = "李白字太白，其父李客，曾任翰林。"
    li_bai = EvidenceSpan(id="p1-m1", quote="李白", start=0, end=2, start_utf16=0, end_utf16=2)
    li_ke_start = context.index("李客")
    li_ke = EvidenceSpan(
        id="p2-m1",
        quote="李客",
        start=li_ke_start,
        end=li_ke_start + 2,
        start_utf16=li_ke_start,
        end_utf16=li_ke_start + 2,
    )
    office_start = context.index("任翰林")
    office = EvidenceSpan(
        id="ev-e1",
        quote="任翰林",
        start=office_start,
        end=office_start + 3,
        start_utf16=office_start,
        end_utf16=office_start + 3,
    )
    label = ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(
                key="p1",
                name_surface="李白",
                level=1,
                level_reason="主人公",
                mentions=[li_bai],
                life_events=[
                    LifeEventAnnotation(
                        key="ev1",
                        event_type="任职",
                        official_title="翰林",
                        evidence=[office],
                    )
                ],
            ),
            PersonAnnotation(
                key="p2",
                name_surface="李客",
                level=2,
                level_reason="父亲",
                mentions=[li_ke],
            ),
        ],
        person_relations=[
            PersonRelationAnnotation(
                key="r1",
                source_person_key="p1",
                target_person_key="p2",
                relation_category="亲属",
                relation_summary="父子",
                codes=["F"],
                reverse_codes=["S"],
                evidence=[li_ke],
            )
        ],
    )
    items = build_ai_annotation_review_items(label)
    by_id = {item.id: item for item in items}
    by_id["person:p2:name_surface"].verdict = "incorrect"
    by_id["person:p2:name_surface"].remove_item = True
    by_id["event:p1:ev1:evidence:0"].verdict = "incorrect"
    by_id["event:p1:ev1:evidence:0"].remove_item = True

    patched = apply_ai_review_to_label(label, items, [], context)

    assert [person.key for person in patched.persons] == ["p1"]
    assert patched.person_relations == []
    assert patched.persons[0].life_events[0].evidence == []


def test_apply_review_can_add_structured_records_and_evidence():
    context = "李白字太白，其父李客，曾任翰林。"
    li_bai = EvidenceSpan(id="p1-m1", quote="李白", start=0, end=2, start_utf16=0, end_utf16=2)
    li_ke_start = context.index("李客")
    li_ke = EvidenceSpan(
        id="new-p2-m1",
        quote="李客",
        start=li_ke_start,
        end=li_ke_start + 2,
        start_utf16=li_ke_start,
        end_utf16=li_ke_start + 2,
    )
    office_start = context.index("任翰林")
    office = EvidenceSpan(
        id="new-ev-e1",
        quote="任翰林",
        start=office_start,
        end=office_start + 3,
        start_utf16=office_start,
        end_utf16=office_start + 3,
    )
    courtesy_start = context.index("字太白")
    courtesy = EvidenceSpan(
        id="new-p1-m2",
        quote="太白",
        start=courtesy_start + 1,
        end=courtesy_start + 3,
        start_utf16=courtesy_start + 1,
        end_utf16=courtesy_start + 3,
    )
    label = ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(
                key="p1",
                name_surface="李白",
                level=1,
                level_reason="主人公",
                mentions=[li_bai],
            )
        ]
    )
    items = build_ai_annotation_review_items(label)
    additions = [
        AIAnnotationReviewOmission(
            id="add-person",
            addition_type="person",
            category="人物",
            evidence=[li_ke],
            data={
                "key": "p2",
                "name_surface": "李客",
                "level": 2,
                "level_reason": "主人公之父",
            },
        ),
        AIAnnotationReviewOmission(
            id="add-event",
            addition_type="life_event",
            category="生平事件",
            parent_key="p2",
            evidence=[office],
            data={
                "key": "ev2",
                "event_type": "任职",
                "official_title": "翰林",
            },
        ),
        AIAnnotationReviewOmission(
            id="add-relation",
            addition_type="person_relation",
            category="人物关系",
            evidence=[li_ke],
            data={
                "key": "r2",
                "source_person_key": "p1",
                "target_person_key": "p2",
                "relation_category": "亲属",
                "relation_summary": "父子",
            },
        ),
        AIAnnotationReviewOmission(
            id="add-evidence",
            addition_type="evidence",
            category="证据",
            evidence=[courtesy],
            data={"target_type": "person", "target_key": "p1"},
        ),
    ]

    patched = apply_ai_review_to_label(label, items, additions, context)

    assert [person.key for person in patched.persons] == ["p1", "p2"]
    assert [item.quote for item in patched.persons[0].mentions] == ["李白", "太白"]
    assert patched.persons[1].life_events[0].official_title == "翰林"
    assert patched.persons[1].event_checks["任职"] == "has_fact"
    assert patched.person_relations[0].target_person_key == "p2"
    assert patched.person_relations[0].relation_category == "亲属"


def test_apply_review_can_add_relation_to_level_three_person():
    context = "李白与故人交游。"
    relation_evidence = EvidenceSpan(
        id="relation-evidence",
        quote="与故人交游",
        start=2,
        end=7,
        start_utf16=2,
        end_utf16=7,
    )
    label = ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(key="p1", name_surface="李白", level=1),
            PersonAnnotation(key="p3", name_surface="故人", level=3),
        ]
    )
    additions = [
        AIAnnotationReviewOmission(
            id="add-level-three-relation",
            addition_type="person_relation",
            category="人物关系",
            evidence=[relation_evidence],
            data={
                "source_person_key": "p1",
                "target_person_key": "p3",
                "relation_category": "交游与往来",
                "relation_summary": "交游",
            },
        )
    ]

    patched = apply_ai_review_to_label(label, [], additions, context)

    assert len(patched.person_relations) == 1
    assert patched.person_relations[0].target_person_key == "p3"
    assert patched.person_relations[0].relation_category == "交游与往来"


def test_review_rejects_deleting_a_scalar_field(review_db):
    db, owner, _stranger, job = review_db
    review = ExtractionAIAnnotationReviewService(db).get_review(job.id, owner)

    with pytest.raises(ExtractionAIAnnotationReviewError, match="不属于可删除"):
        ExtractionAIAnnotationReviewService(db).save_review(
            job.id,
            AIAnnotationReviewUpdateRequest(
                decisions=[
                    AIAnnotationReviewDecision(
                        item_id="passage:source_type",
                        verdict="incorrect",
                        remove_item=True,
                    )
                ]
            ),
            owner,
        )


def test_review_accepts_deleting_a_whole_person_group(review_db):
    db, owner, _stranger, job = review_db
    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    person_item = next(item for item in review.items if item.id.startswith("person:"))
    person_group = person_item.group_key
    person_ids = [item.id for item in review.items if item.group_key == person_group]

    saved = service.save_review(
        job.id,
        AIAnnotationReviewUpdateRequest(
            decisions=[
                AIAnnotationReviewDecision(
                    item_id=item_id,
                    verdict="incorrect",
                    remove_item=True,
                )
                for item_id in person_ids
            ]
        ),
        owner,
    )

    saved_by_id = {item.id: item for item in saved.items}
    assert all(saved_by_id[item_id].remove_item for item_id in person_ids)


def test_empty_excluded_mention_is_dropped_before_review_submission(review_db):
    db, owner, _stranger, job = review_db
    result_payload = json.loads(job.result_json or "{}")
    result_payload["document"]["label"]["excluded_mentions"] = [
        {"key": "x1", "reason": "", "evidence": []}
    ]
    job.result_json = json.dumps(result_payload, ensure_ascii=False)
    db.add(job)
    db.commit()

    service = ExtractionAIAnnotationReviewService(db)
    review = service.get_review(job.id, owner)
    excluded_items = [
        item for item in review.items if item.path.startswith("excluded_mentions.")
    ]
    assert excluded_items == []

    decisions = _all_correct(review)

    submitted = service.submit_review(
        job.id,
        AIAnnotationReviewUpdateRequest(decisions=decisions),
        owner,
    )

    assert submitted.status == "submitted"
    assert submitted.ground_truth_document is not None
    assert submitted.ground_truth_document.label.excluded_mentions == []


def test_ai_job_response_drops_legacy_meaningless_excluded_mention(review_db):
    db, owner, _stranger, job = review_db
    result_payload = json.loads(job.result_json or "{}")
    result_payload["document"]["label"]["excluded_mentions"] = [
        {"key": "x1", "reason": "", "evidence": []}
    ]
    job.result_json = json.dumps(result_payload, ensure_ascii=False)
    db.add(job)
    db.commit()

    response = ExtractionAIAnnotationJobService(db).get_job(job.id, owner)

    assert response.result is not None
    assert response.result.document is not None
    assert response.result.document.label.excluded_mentions == []
