from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationSavedResult,
)
from app.models.extraction_annotation import ExtractionAnnotationTask
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationRepairRequest,
    ExtractionTaskCreateRequest,
)
from app.services import extraction_ai_annotation_job_service as job_module
from app.services.extraction_ai_annotation_job_service import (
    ExtractionAIAnnotationJobError,
    ExtractionAIAnnotationJobService,
)
from app.services.extraction_ai_annotation_service import (
    AIAnnotationServiceError,
    ExtractionAIAnnotationService,
    persist_ai_annotation_result,
)
from app.services.extraction_annotation_service import ExtractionAnnotationService


@pytest.fixture()
def generation_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    db = local_session()
    admin = User(id=1, email="admin-generations@example.com", password_hash="x", role="admin")
    passage = Passage(
        title="多份生成篇目",
        context="李白字太白，少有逸才。",
        source_type="manual_input",
        created_by=admin.id,
        workflow_status="success",
    )
    db.add_all([admin, passage])
    db.commit()
    db.refresh(passage)
    task = ExtractionAnnotationService(db).create_task(
        ExtractionTaskCreateRequest(passage_id=passage.doc_id),
        admin,
    )
    yield db, admin, task
    db.close()
    engine.dispose()


def _success_job(task: ExtractionAnnotationTask, user: User, **overrides) -> ExtractionAIAnnotationJob:
    payload = {
        "task_id": task.id,
        "passage_id": task.passage_id,
        "requested_by": user.id,
        "operation": "generate",
        "status": "success",
        "provider": "mock",
        "model": "mock-ai-annotation",
        "result_json": "{}",
    }
    payload.update(overrides)
    return ExtractionAIAnnotationJob(**payload)


def test_two_generations_keep_separate_working_copies(generation_db):
    db, admin, task = generation_db
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task.id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    first = _success_job(task, admin, provider="deepseek", model="deepseek-chat")
    second = _success_job(task, admin, provider="", model="")
    db.add_all([first, second])
    db.commit()
    other = generated.model_copy(update={"raw_content": "second-model-output", "model": "other"})

    persist_ai_annotation_result(
        db,
        generated,
        requested_by=admin.id,
        source_job_id=first.id,
        lineage_job_id=first.id,
    )
    persist_ai_annotation_result(
        db,
        other,
        requested_by=admin.id,
        source_job_id=second.id,
        lineage_job_id=second.id,
    )
    db.commit()

    rows = db.query(ExtractionAIAnnotationSavedResult).all()
    assert {row.lineage_job_id for row in rows} == {first.id, second.id}
    by_lineage = {row.lineage_job_id: json.loads(row.result_json)["raw_content"] for row in rows}
    assert by_lineage[first.id] != by_lineage[second.id]

    listed = ExtractionAIAnnotationJobService(db).list_generations(task.id, admin)
    assert listed.generation_count == 2
    assert [(group.provider, group.model) for group in listed.groups] == [
        ("", ""),
        ("deepseek", "deepseek-chat"),
    ]


def test_generations_of_an_existing_task_can_be_empty(generation_db):
    db, admin, task = generation_db

    listed = ExtractionAIAnnotationJobService(db).list_generations(task.id, admin)

    assert listed.task_id == task.id
    assert listed.groups == []
    assert listed.generation_count == 0
    assert listed.active_job_id is None


def test_generations_missing_task_is_not_found(generation_db):
    db, admin, _task = generation_db

    with pytest.raises(ExtractionAIAnnotationJobError) as exc_info:
        ExtractionAIAnnotationJobService(db).list_generations(999_999, admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "未找到指定的标注任务"


def test_archive_rejects_an_in_flight_repair(generation_db):
    db, admin, task = generation_db
    root = _success_job(task, admin)
    db.add(root)
    db.commit()
    repair = ExtractionAIAnnotationJob(
        task_id=task.id,
        passage_id=task.passage_id,
        requested_by=admin.id,
        operation="repair",
        source_job_id=root.id,
        status="running",
        provider="mock",
        model="mock-ai-annotation",
    )
    db.add(repair)
    db.commit()

    with pytest.raises(ExtractionAIAnnotationJobError) as exc_info:
        ExtractionAIAnnotationJobService(db).archive_generation(root.id, admin)

    assert exc_info.value.status_code == 409
    db.refresh(root)
    assert root.archived_at is None


def test_active_job_is_reused_before_the_generation_cap(generation_db, monkeypatch):
    db, admin, task = generation_db
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)
    original_limit = job_module.settings.ai_annotation_max_generations_per_task
    job_module.settings.ai_annotation_max_generations_per_task = 1
    try:
        service = ExtractionAIAnnotationJobService(db)
        payload = AIAnnotationGenerateRequest(
            task_id=task.id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
        first = service.enqueue(payload, admin)
        second = service.enqueue(payload, admin)

        assert first.reused_existing is False
        assert second.reused_existing is True
        assert second.id == first.id
        assert db.query(ExtractionAIAnnotationJob).count() == 1

        queued = db.get(ExtractionAIAnnotationJob, first.id)
        assert queued is not None
        queued.status = "success"
        db.add(queued)
        db.commit()

        with pytest.raises(ExtractionAIAnnotationJobError) as exc_info:
            service.enqueue(payload, admin)

        assert exc_info.value.status_code == 409
        assert "已达上限 1" in str(exc_info.value.detail)
        assert db.query(ExtractionAIAnnotationJob).count() == 1
    finally:
        job_module.settings.ai_annotation_max_generations_per_task = original_limit


def test_failed_root_cannot_be_validated_or_repaired(generation_db):
    db, admin, task = generation_db
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task.id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    failed = _success_job(task, admin, status="failed", result_json=None)
    db.add(failed)
    db.commit()
    content = json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False)
    service = ExtractionAIAnnotationService(db)

    with pytest.raises(AIAnnotationServiceError) as validate_error:
        service.validate(task.id, content, requested_by=admin.id, job_id=failed.id)
    assert validate_error.value.status_code == 409

    with pytest.raises(AIAnnotationServiceError) as repair_error:
        service.repair(
            AIAnnotationRepairRequest(
                task_id=task.id,
                content=content,
                config=AIAnnotationConfigRequest(provider="mock"),
                job_id=failed.id,
            ),
            requested_by=admin.id,
        )
    assert repair_error.value.status_code == 409
    assert db.query(ExtractionAIAnnotationSavedResult).count() == 0


def test_cloned_generation_does_not_consume_the_slot(generation_db, monkeypatch):
    db, admin, task = generation_db
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)
    source_owner = User(id=2, email="source-owner@example.com", password_hash="x", role="admin")
    db.add(source_owner)
    db.commit()
    source = _success_job(task, source_owner, provider="openai_compatible", model="source")
    db.add(source)
    db.commit()
    clone = _success_job(
        task,
        admin,
        provider="openai_compatible",
        model="source",
        cloned_from_job_id=source.id,
    )
    db.add(clone)
    db.commit()
    original_limit = job_module.settings.ai_annotation_max_generations_per_task
    job_module.settings.ai_annotation_max_generations_per_task = 1
    try:
        created = ExtractionAIAnnotationJobService(db).enqueue(
            AIAnnotationGenerateRequest(
                task_id=task.id,
                config=AIAnnotationConfigRequest(provider="mock"),
            ),
            admin,
        )
    finally:
        job_module.settings.ai_annotation_max_generations_per_task = original_limit

    assert created.reused_existing is False
    assert created.id != clone.id
    assert created.active_generation_count == 1
