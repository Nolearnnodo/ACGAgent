from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
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
