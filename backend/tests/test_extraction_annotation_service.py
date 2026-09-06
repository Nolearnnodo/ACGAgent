from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_annotation import (
    ExtractionAdjudicationDraft,
    ExtractionAnnotationSubmission,
    ExtractionGoldVersion,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    EvidenceSpan,
    ExtractionAnnotationLabel,
    ExtractionTaskCreateRequest,
    PersonAnnotation,
)
from app.services.extraction_annotation_service import (
    AnnotationConflictError,
    AnnotationForbiddenError,
    AnnotationValidationError,
    ExtractionAnnotationService,
    codepoint_to_utf16_offset,
    validate_evidence_offsets,
)


@pytest.fixture()
def annotation_db() -> tuple[Session, User, User, User, Passage]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    db = local_session()
    admin = User(id=1, email="admin@example.com", password_hash="x", role="admin")
    annotator_a = User(id=2, email="a@example.com", password_hash="x", role="user")
    annotator_b = User(id=3, email="b@example.com", password_hash="x", role="user")
    passage = Passage(
        title="李承传",
        context="李𠮷，字敬之。父李彦，太常少卿。",
        source_type="manual_input",
        created_by=admin.id,
        workflow_status="success",
    )
    db.add_all([admin, annotator_a, annotator_b, passage])
    db.commit()
    db.refresh(passage)
    yield db, admin, annotator_a, annotator_b, passage
    db.close()
    engine.dispose()


def _person_label(context: str) -> ExtractionAnnotationLabel:
    start = 0
    end = 2
    mention = EvidenceSpan(
        id="e-person-1",
        quote=context[start:end],
        start=start,
        end=end,
        start_utf16=codepoint_to_utf16_offset(context, start),
        end_utf16=codepoint_to_utf16_offset(context, end),
    )
    return ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(
                key="person-1",
                name_surface="李𠮷",
                level=1,
                level_reason="单人列传传主",
                mentions=[mention],
                event_checks={
                    "出生": "not_mentioned",
                    "籍贯": "not_mentioned",
                    "死亡": "not_mentioned",
                    "埋葬": "not_mentioned",
                    "任职": "not_mentioned",
                },
            )
        ]
    )


def test_utf16_offset_is_validated_for_extension_b_character():
    context = "李𠮷任华州刺史"
    label = _person_label(context)

    assert label.persons[0].mentions[0].end == 2
    assert label.persons[0].mentions[0].end_utf16 == 3
    assert validate_evidence_offsets(label, context) == []

    label.persons[0].mentions[0].end_utf16 = 2
    issues = validate_evidence_offsets(label, context)
    assert issues[0]["code"] == "evidence_utf16_mismatch"


def test_claimed_submissions_are_blind_and_revision_is_optimistic(annotation_db):
    db, admin, annotator_a, annotator_b, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    assert task.claimed_count == 0
    assert task.available_slots == 2
    assert task.submission_id is None
    assert task.assignments == []

    with pytest.raises(AnnotationForbiddenError):
        service.get_task_detail(task.id, annotator_a)

    detail_a = service.claim_task(task.id, annotator_a)
    assert detail_a.submission.slot_no == 1
    saved_a = service.save_draft(
        task.id,
        annotator_a,
        detail_a.submission.revision,
        _person_label(passage.context),
    )
    assert saved_a.submission.revision == 1

    with pytest.raises(AnnotationConflictError):
        service.save_draft(task.id, annotator_a, 0, _person_label(passage.context))

    detail_b = service.claim_task(task.id, annotator_b)
    assert detail_b.submission.slot_no == 2
    assert detail_b.submission.label.persons == []

    submitted = service.submit(
        task.id,
        annotator_a,
        saved_a.submission.revision,
        saved_a.submission.label,
    )
    assert submitted.submission.state == "submitted"
    assert submitted.submission.revision == 2


def test_admin_can_see_assignees_and_release_only_unsubmitted_drafts(annotation_db):
    db, admin, annotator_a, annotator_b, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    claimed = service.claim_task(task.id, annotator_a)

    admin_summary = service.list_tasks(admin)[0]
    assert len(admin_summary.assignments) == 1
    assert admin_summary.assignments[0].submission_id == claimed.submission.id
    assert admin_summary.assignments[0].annotator_id == annotator_a.id
    assert admin_summary.assignments[0].annotator_email == annotator_a.email
    assert admin_summary.assignments[0].state == "draft"

    annotator_summary = service.list_tasks(annotator_b)[0]
    assert annotator_summary.assignments == []

    with pytest.raises(AnnotationForbiddenError):
        service.release_draft(task.id, claimed.submission.id, annotator_b)

    released = service.release_draft(task.id, claimed.submission.id, admin)
    assert released.status == "open"
    assert released.claimed_count == 0
    assert released.available_slots == 2
    assert released.assignments == []

    replacement = service.claim_task(task.id, annotator_b)
    assert replacement.submission.slot_no == 1
    submitted = service.submit(
        task.id,
        annotator_b,
        replacement.submission.revision,
        _person_label(passage.context),
    )
    with pytest.raises(AnnotationConflictError):
        service.release_draft(task.id, submitted.submission.id, admin)


def test_admin_can_reset_task_and_clear_all_annotation_data(annotation_db):
    db, admin, annotator_a, annotator_b, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    detail_a = service.claim_task(task.id, annotator_a)
    service.submit(task.id, annotator_a, detail_a.submission.revision, _person_label(passage.context))
    service.claim_task(task.id, annotator_b)
    db.add(
        ExtractionAdjudicationDraft(
            task_id=task.id,
            reviewer_id=admin.id,
            resolutions_json="[]",
            gold_json="{}",
        )
    )
    db.add(
        ExtractionGoldVersion(
            task_id=task.id,
            version=1,
            gold_json="{}",
            source_submission_ids_json="[]",
            adjudication_log_json="[]",
            reviewer_id=admin.id,
        )
    )
    db.commit()

    with pytest.raises(AnnotationForbiddenError):
        service.reset_task(task.id, annotator_a)

    reset = service.reset_task(task.id, admin)
    assert reset.status == "open"
    assert reset.claimed_count == 0
    assert reset.submitted_count == 0
    assert reset.available_slots == 2
    assert db.query(ExtractionAnnotationSubmission).filter_by(task_id=task.id).count() == 0
    assert db.query(ExtractionAdjudicationDraft).filter_by(task_id=task.id).count() == 0
    assert db.query(ExtractionGoldVersion).filter_by(task_id=task.id).count() == 0
    assert db.get(Passage, passage.doc_id) is not None


def test_admin_can_delete_task_without_deleting_passage(annotation_db):
    db, admin, annotator_a, _, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    service.claim_task(task.id, annotator_a)

    with pytest.raises(AnnotationForbiddenError):
        service.delete_task(task.id, annotator_a)

    service.delete_task(task.id, admin)
    assert db.query(ExtractionAnnotationSubmission).filter_by(task_id=task.id).count() == 0
    assert db.get(Passage, passage.doc_id) is not None
    assert service.list_tasks(admin) == []


def test_draft_rejects_evidence_that_cannot_replay(annotation_db):
    db, admin, annotator_a, _, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    detail = service.claim_task(task.id, annotator_a)
    label = _person_label(passage.context)
    label.persons[0].mentions[0].quote = "错误文字"

    with pytest.raises(AnnotationValidationError) as error:
        service.save_draft(
            task.id,
            annotator_a,
            detail.submission.revision,
            label,
        )

    assert error.value.detail[0]["code"] == "evidence_quote_mismatch"


def test_import_result_into_current_draft(annotation_db):
    db, admin, annotator_a, _, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(ExtractionTaskCreateRequest(passage_id=passage.doc_id), admin)
    detail = service.claim_task(task.id, annotator_a)
    label = _person_label(passage.context)
    payload = {
        "metadata": {
            "task_id": task.id,
            "spec_version": task.spec_version,
            "context_sha256": task.context_sha256,
        },
        "passage": {
            "doc_id": passage.doc_id,
            "context": passage.context,
        },
        "label": label.model_dump(mode="json"),
    }

    imported = service.import_draft(
        task.id,
        annotator_a,
        detail.submission.revision,
        json.dumps(payload, ensure_ascii=False),
    )

    assert imported.submission.revision == 1
    assert imported.submission.state == "draft"
    assert imported.submission.label.persons[0].name_surface == "李𠮷"


def test_ai_import_keeps_rule_errors_and_discards_out_of_range_evidence(annotation_db):
    db, admin, annotator_a, _, passage = annotation_db
    service = ExtractionAnnotationService(db)
    task = service.create_task(ExtractionTaskCreateRequest(passage_id=passage.doc_id), admin)
    detail = service.claim_task(task.id, annotator_a)
    label = _person_label(passage.context)
    label.persons[0].event_checks["出生"] = "has_fact"
    label.persons[0].mentions[0].end = len(passage.context) + 10
    label.persons[0].mentions[0].end_utf16 = len(passage.context) + 10
    payload = {
        "metadata": {
            "task_id": task.id,
            "spec_version": task.spec_version,
            "context_sha256": task.context_sha256,
        },
        "passage": {"doc_id": passage.doc_id, "context": passage.context},
        "label": label.model_dump(mode="json"),
    }

    imported = service.import_draft(
        task.id,
        annotator_a,
        detail.submission.revision,
        json.dumps(payload, ensure_ascii=False),
        allow_validation_issues=True,
    )

    assert imported.submission.label.persons[0].mentions == []
    assert imported.submission.label.persons[0].event_checks["出生"] == "has_fact"

    with pytest.raises(AnnotationValidationError):
        service.import_draft(
            task.id,
            annotator_a,
            imported.submission.revision,
            "not-json",
            allow_validation_issues=True,
        )
