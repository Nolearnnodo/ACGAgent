from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_annotation import ExtractionGoldVersion
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AdjudicationResolution,
    EvidenceSpan,
    ExtractionAdjudicationUpdateRequest,
    ExtractionAnnotationLabel,
    ExtractionTaskCreateRequest,
    PersonAnnotation,
    PersonRelationAnnotation,
)
from app.services.extraction_adjudication_service import ExtractionAdjudicationService
from app.services.extraction_annotation_service import (
    AnnotationConflictError,
    AnnotationValidationError,
    ExtractionAnnotationService,
    codepoint_to_utf16_offset,
    relation_reverse_suggestions,
    validate_for_submission,
)


@pytest.fixture()
def adjudication_db() -> tuple[Session, User, User, User, Passage]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = User(id=1, email="admin@example.com", password_hash="x", role="admin")
    annotator_a = User(id=2, email="a@example.com", password_hash="x", role="user")
    annotator_b = User(id=3, email="b@example.com", password_hash="x", role="user")
    passage = Passage(
        title="李𠮷传",
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


def _evidence(context: str, start: int, end: int, evidence_id: str) -> EvidenceSpan:
    return EvidenceSpan(
        id=evidence_id,
        quote=context[start:end],
        start=start,
        end=end,
        start_utf16=codepoint_to_utf16_offset(context, start),
        end_utf16=codepoint_to_utf16_offset(context, end),
    )


def _label(context: str, *, reason: str, key_suffix: str) -> ExtractionAnnotationLabel:
    return ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(
                key=f"person-{key_suffix}",
                name_surface="李𠮷",
                level=1,
                level_reason=reason,
                mentions=[_evidence(context, 0, 2, f"mention-{key_suffix}")],
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


def _submitted_task(
    db: Session,
    admin: User,
    annotator_a: User,
    annotator_b: User,
    passage: Passage,
):
    service = ExtractionAnnotationService(db)
    task = service.create_task(ExtractionTaskCreateRequest(passage_id=passage.doc_id), admin)
    for annotator, reason, suffix in (
        (annotator_a, "A：单人列传传主", "a"),
        (annotator_b, "B：题名与正文共同确认", "b"),
    ):
        detail = service.claim_task(task.id, annotator)
        service.submit(
            task.id,
            annotator,
            detail.submission.revision,
            _label(passage.context, reason=reason, key_suffix=suffix),
        )
    return task


def test_adjudication_draft_lock_version_and_yaml_export(adjudication_db):
    db, admin, annotator_a, annotator_b, passage = adjudication_db
    task = _submitted_task(db, admin, annotator_a, annotator_b, passage)
    service = ExtractionAdjudicationService(db)

    detail = service.get_adjudication(task.id)
    assert detail.task_status == "ready_for_adjudication"
    assert len(detail.submissions) == 2
    assert all(not hasattr(item, "annotator_id") for item in detail.submissions)
    difference = next(item for item in detail.differences if item.path.endswith("level_reason"))

    resolutions = [
        AdjudicationResolution(difference_id=item.id, decision="b")
        for item in detail.differences
    ]
    saved = service.save_draft(
        task.id,
        ExtractionAdjudicationUpdateRequest(revision=0, resolutions=resolutions),
        admin,
    )
    assert saved.draft.revision == 1
    assert saved.draft.gold_label.persons[0].level_reason.startswith("B：")

    locked = service.lock_gold(
        task.id,
        ExtractionAdjudicationUpdateRequest(revision=1, resolutions=resolutions),
        admin,
    )
    assert locked.task_status == "completed"
    assert locked.latest_gold is not None
    assert locked.latest_gold.version == 1
    assert locked.latest_gold.label.persons[0].level_reason == difference.value_b

    filename, content = service.export_gold_yaml(task.id)
    exported = yaml.safe_load(content)
    assert filename == f"extraction-gold-task-{task.id}-v1.yaml"
    assert exported["passage"]["title"] == "李𠮷传"
    assert exported["label"]["persons"][0]["name_surface"] == "李𠮷"

    first_gold = db.query(ExtractionGoldVersion).filter_by(task_id=task.id, version=1).one()
    first_json = first_gold.gold_json
    with pytest.raises(AnnotationValidationError):
        service.lock_gold(
            task.id,
            ExtractionAdjudicationUpdateRequest(
                revision=locked.draft.revision,
                resolutions=resolutions,
            ),
            admin,
        )
    second = service.lock_gold(
        task.id,
        ExtractionAdjudicationUpdateRequest(
            revision=locked.draft.revision,
            resolutions=resolutions,
            change_reason="复核分级依据措辞",
        ),
        admin,
    )
    assert second.latest_gold is not None and second.latest_gold.version == 2
    db.refresh(first_gold)
    assert first_gold.gold_json == first_json
    assert json.loads(first_gold.adjudication_log_json)["version"] == 1


def test_gold_import_is_idempotent_and_available_to_downstream(adjudication_db):
    db, admin, annotator_a, annotator_b, passage = adjudication_db
    task = _submitted_task(db, admin, annotator_a, annotator_b, passage)
    service = ExtractionAdjudicationService(db)
    detail = service.get_adjudication(task.id)
    resolutions = [
        AdjudicationResolution(difference_id=item.id, decision="a")
        for item in detail.differences
    ]
    service.lock_gold(
        task.id,
        ExtractionAdjudicationUpdateRequest(revision=0, resolutions=resolutions),
        admin,
    )
    _, exported = service.export_gold_yaml(task.id)

    imported = service.import_gold_yaml(task.id, exported, admin, source_name="previous.yaml")
    assert imported.version == 1
    assert imported.label.persons[0].name_surface == "李𠮷"
    assert service.get_gold(task.id).version == 1
    assert db.query(ExtractionGoldVersion).filter_by(task_id=task.id).count() == 1


def test_gold_import_rejects_a_changed_context(adjudication_db):
    db, admin, annotator_a, annotator_b, passage = adjudication_db
    task = _submitted_task(db, admin, annotator_a, annotator_b, passage)
    service = ExtractionAdjudicationService(db)
    detail = service.get_adjudication(task.id)
    resolutions = [
        AdjudicationResolution(difference_id=item.id, decision="a")
        for item in detail.differences
    ]
    service.lock_gold(
        task.id,
        ExtractionAdjudicationUpdateRequest(revision=0, resolutions=resolutions),
        admin,
    )
    _, exported = service.export_gold_yaml(task.id)
    document = yaml.safe_load(exported)
    document["metadata"]["context_sha256"] = "0" * 64

    with pytest.raises(AnnotationConflictError):
        service.import_gold_yaml(task.id, yaml.safe_dump(document, allow_unicode=True), admin)


def test_relation_reverse_suggestions_and_submission_validation():
    suggestions = relation_reverse_suggestions(["F", "W"])
    assert suggestions == [["H", "S"], ["H", "D"]]

    context = "甲父乙"
    label = ExtractionAnnotationLabel(
        persons=[
            PersonAnnotation(
                key="a",
                name_surface="甲",
                level=1,
                level_reason="传主",
                mentions=[_evidence(context, 0, 1, "a-e")],
                event_checks={event: "not_mentioned" for event in ("出生", "籍贯", "死亡", "埋葬", "任职")},
            ),
            PersonAnnotation(
                key="b",
                name_surface="乙",
                level=2,
                level_reason="父子关系",
                mentions=[_evidence(context, 2, 3, "b-e")],
                event_checks={event: "not_mentioned" for event in ("出生", "籍贯", "死亡", "埋葬", "任职")},
            ),
        ],
        person_relations=[
            PersonRelationAnnotation(
                key="r",
                source_person_key="a",
                target_person_key="b",
                codes=["F"],
                reverse_codes=["F"],
                evidence=[_evidence(context, 0, 3, "r-e")],
            )
        ],
    )
    issues = validate_for_submission(label, context, label.schema_version)
    assert any(item["code"] == "relation_inverse_mismatch" for item in issues)
