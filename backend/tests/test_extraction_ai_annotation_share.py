from __future__ import annotations

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
    ExtractionTaskCreateRequest,
)
from app.services.auth_service import AuthService
from app.services.extraction_ai_annotation_job_service import ExtractionAIAnnotationJobService
from app.services.extraction_ai_annotation_metrics_service import (
    ExtractionAIAnnotationMetricsService,
)
from app.services.extraction_ai_annotation_service import (
    ExtractionAIAnnotationService,
    persist_ai_annotation_result,
)
from app.services.extraction_ai_annotation_share_service import (
    CANONICAL_AI_ANNOTATION_EMAIL,
    ExtractionAIAnnotationShareService,
)
from app.services.extraction_annotation_service import ExtractionAnnotationService


@pytest.fixture()
def share_db() -> tuple[Session, User, User, ExtractionAIAnnotationJob]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = User(
        id=1,
        email=CANONICAL_AI_ANNOTATION_EMAIL,
        password_hash="x",
        role="admin",
    )
    owner = User(id=2, email="owner@example.com", password_hash="x", role="user")
    passage = Passage(
        title="李白小传",
        context="李白字太白，少有逸才。",
        source_type="manual_input",
        created_by=admin.id,
        workflow_status="success",
    )
    db.add_all([admin, owner, passage])
    db.commit()
    db.refresh(passage)
    task = ExtractionAnnotationService(db).create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task.id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    admin_job = ExtractionAIAnnotationJob(
        task_id=task.id,
        passage_id=passage.doc_id,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
        first_round_validation_status=generated.validation_status,
    )
    db.add(admin_job)
    db.commit()
    db.refresh(admin_job)
    persist_ai_annotation_result(
        db,
        generated,
        requested_by=admin.id,
        source_job_id=admin_job.id,
        lineage_job_id=admin_job.id,
    )
    db.commit()
    yield db, admin, owner, admin_job
    db.close()
    engine.dispose()


def _private_job(db: Session, owner: User, admin_job: ExtractionAIAnnotationJob) -> ExtractionAIAnnotationJob:
    private = ExtractionAIAnnotationJob(
        task_id=admin_job.task_id,
        passage_id=admin_job.passage_id,
        requested_by=owner.id,
        operation="generate",
        status="success",
        provider="mock",
        model="owner-private",
        result_json=admin_job.result_json,
    )
    db.add(private)
    db.commit()
    db.refresh(private)
    return private


def test_backup_keeps_each_account_private_jobs(share_db):
    db, admin, owner, admin_job = share_db
    private = _private_job(db, owner, admin_job)

    payload = ExtractionAIAnnotationShareService(db).backup_private_annotations()
    by_email = {item["email"]: item for item in payload["users"]}

    assert {job["id"] for job in by_email[admin.email]["jobs"]} == {admin_job.id}
    assert {job["id"] for job in by_email[owner.email]["jobs"]} == {private.id}
    assert by_email[admin.email]["saved_results"]


def test_sync_copies_admin_results_to_other_accounts_without_deleting_private_jobs(share_db):
    db, _admin, owner, admin_job = share_db
    private = _private_job(db, owner, admin_job)

    summary = ExtractionAIAnnotationShareService(db).sync_all_users()
    db.commit()

    jobs = ExtractionAIAnnotationJobService(db).list_latest_jobs(owner)
    saved = (
        db.query(ExtractionAIAnnotationSavedResult)
        .filter(
            ExtractionAIAnnotationSavedResult.task_id == admin_job.task_id,
            ExtractionAIAnnotationSavedResult.requested_by == owner.id,
        )
        .one()
    )
    cloned = db.get(ExtractionAIAnnotationJob, jobs[0].id)

    assert summary["copied_users"] == 1
    assert summary["copied_jobs"] == 1
    assert db.get(ExtractionAIAnnotationJob, private.id) is not None
    assert cloned is not None
    assert cloned.requested_by == owner.id
    assert cloned.cloned_from_job_id == admin_job.id
    assert jobs[0].saved_result is not None
    assert saved.source_job_id == cloned.id
    assert json.loads(cloned.result_json or "{}") == json.loads(admin_job.result_json or "{}")


def test_sync_is_idempotent_for_the_same_admin_job(share_db):
    db, _admin, owner, _admin_job = share_db
    service = ExtractionAIAnnotationShareService(db)
    service.sync_all_users()
    db.commit()
    first_count = (
        db.query(ExtractionAIAnnotationJob)
        .filter(ExtractionAIAnnotationJob.requested_by == owner.id)
        .count()
    )

    service.sync_all_users()
    db.commit()

    assert (
        db.query(ExtractionAIAnnotationJob)
        .filter(ExtractionAIAnnotationJob.requested_by == owner.id)
        .count()
        == first_count
    )


def test_metrics_do_not_count_cloned_admin_jobs(share_db):
    db, _admin, _owner, _admin_job = share_db
    ExtractionAIAnnotationShareService(db).sync_all_users()
    db.commit()

    metrics = ExtractionAIAnnotationMetricsService(db).get_metrics(days=0)

    assert metrics.summary.session_count == 1
    assert {record.requested_by_email for record in metrics.records} == {
        CANONICAL_AI_ANNOTATION_EMAIL
    }


def test_register_copies_admin_annotations_to_new_accounts(share_db):
    db, _admin, _owner, admin_job = share_db

    created = AuthService(db).register("new-user@example.com", "password123")
    jobs = ExtractionAIAnnotationJobService(db).list_latest_jobs(created)

    assert created.email == "new-user@example.com"
    assert len(jobs) == 1
    assert jobs[0].task_id == admin_job.task_id
    assert jobs[0].status == "success"
    assert jobs[0].saved_result is not None
    cloned = db.get(ExtractionAIAnnotationJob, jobs[0].id)
    assert cloned is not None
    assert cloned.cloned_from_job_id == admin_job.id


def test_new_user_can_open_review_on_cloned_admin_job(share_db):
    db, _admin, owner, _admin_job = share_db
    ExtractionAIAnnotationShareService(db).sync_user(owner)
    db.commit()
    job = ExtractionAIAnnotationJobService(db).list_latest_jobs(owner)[0]

    from app.services.extraction_ai_annotation_review_service import (
        ExtractionAIAnnotationReviewService,
    )

    review = ExtractionAIAnnotationReviewService(db).get_review(job.id, owner)
    assert review.generation_job_id == job.id
    assert review.status == "draft"
