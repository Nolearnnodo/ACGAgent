from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationSavedResult,
)
from app.models.extraction_annotation import ExtractionAnnotationTask, ExtractionGoldVersion
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    EVENT_TYPES,
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationJobResponse,
    AIAnnotationPromptOverrides,
    AIAnnotationPromptRequest,
    AIAnnotationRepairRequest,
    ExtractionTaskCreateRequest,
)
from app.services.extraction_ai_annotation_service import (
    AIAnnotationConfigError,
    ExtractionAIAnnotationService,
    parse_ai_annotation_result,
)
from app.services.extraction_ai_annotation_prompt import (
    AI_ANNOTATION_EVENT_FRAGMENT_SYSTEM_PROMPT,
    AI_ANNOTATION_FORMAT_REQUIREMENTS,
    AI_ANNOTATION_FORMAT_REVIEW_SYSTEM_PROMPT,
    AI_ANNOTATION_SYSTEM_PROMPT,
)
from app.services import extraction_ai_annotation_job_service as job_module
from app.services.extraction_ai_annotation_job_service import (
    ExtractionAIAnnotationJobService,
    _run_job_background,
)
from app.services.extraction_annotation_service import ExtractionAnnotationService, context_sha256


@pytest.fixture()
def ai_annotation_db() -> tuple[Session, int]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    db = local_session()
    admin = User(id=1, email="admin-ai@example.com", password_hash="x", role="admin")
    passage = Passage(
        title="AI 测试篇目",
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
    yield db, task.id
    db.close()
    engine.dispose()


def test_mock_generation_returns_importable_document(ai_annotation_db):
    db, task_id = ai_annotation_db
    result = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )

    assert result.validation_status == "valid"
    assert result.document is not None
    assert result.document.metadata.task_id == task_id
    assert result.document.label.persons[0].mentions[0].quote == "李白"
    assert result.validation_issues == []


def test_remote_generation_releases_sqlite_before_model_call(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    seen: dict[str, bool] = {}

    def fake_generate(_task, _passage, _config, _provider, prompt_overrides=None):
        seen["in_transaction"] = service.db.in_transaction()
        return {"label": {"persons": [], "person_relations": []}}, "annotation-model", 1, [], []

    monkeypatch.setattr(service, "_build_provider", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(service, "_generate_staged_document", fake_generate)

    service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert seen["in_transaction"] is False


def test_prompt_preview_uses_spec_prompt_and_current_passage(ai_annotation_db):
    db, task_id = ai_annotation_db
    result = ExtractionAIAnnotationService(db).preview_prompt(
        AIAnnotationPromptRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(
                provider="mock",
                extra_instruction="重点检查任职事件。",
            ),
        )
    )

    assert result.system_prompt == AI_ANNOTATION_SYSTEM_PROMPT
    assert "只使用 title、context 和系统提供的年号/历史事件字典" in result.system_prompt
    user_payload = json.loads(result.user_prompt)
    assert user_payload["title"] == "AI 测试篇目"
    assert user_payload["context"] == "李白字太白，少有逸才。"
    assert user_payload["extra_instruction"] == "重点检查任职事件。"


def test_prompt_overrides_are_used_only_for_current_message_build(ai_annotation_db):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    task, passage = service._get_task_and_passage(task_id)

    messages = service._build_messages(
        task,
        passage,
        AIAnnotationConfigRequest(provider="mock"),
        prompt_overrides=AIAnnotationPromptOverrides(
            system_prompt="临时 system prompt",
            user_prompt="临时 user prompt",
        ),
    )

    assert messages == [
        {"role": "system", "content": "临时 system prompt"},
        {"role": "user", "content": "临时 user prompt"},
    ]


def test_event_fragment_prompt_requires_replayable_evidence_for_has_fact():
    prompt = AI_ANNOTATION_EVENT_FRAGMENT_SYSTEM_PROMPT

    assert 'event_check="has_fact"' in prompt
    assert "life_events 必须返回一条对应 event_type 的事件" in prompt
    assert "必须包含 1 至 8 条非空、可按 quote/start/end 回放的 evidence" in prompt
    assert "找不到上述有效正文证据时，禁止填写 has_fact" in prompt


def test_validation_reports_evidence_problem(ai_annotation_db):
    db, task_id = ai_annotation_db
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    document = generated.document.model_dump(mode="json")
    document["label"]["persons"][0]["mentions"][0]["quote"] = "错误证据"

    result = ExtractionAIAnnotationService(db).validate(
        task_id,
        json.dumps(document, ensure_ascii=False),
    )

    assert result.validation_status == "invalid_rules"
    assert any(issue.code == "evidence_quote_mismatch" for issue in result.validation_issues)


def test_validation_rejects_non_json_output(ai_annotation_db):
    db, task_id = ai_annotation_db

    result = ExtractionAIAnnotationService(db).validate(task_id, "这不是 JSON")

    assert result.validation_status == "invalid_format"
    assert result.document is None
    assert result.validation_issues[0].code == "invalid_json"


def test_generated_document_can_be_imported_into_blind_draft(ai_annotation_db):
    db, task_id = ai_annotation_db
    annotator = User(id=2, email="ai-annotator@example.com", password_hash="x", role="user")
    db.add(annotator)
    db.commit()

    task_service = ExtractionAnnotationService(db)
    detail = task_service.claim_task(task_id, annotator)
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None

    imported = task_service.import_draft(
        task_id,
        annotator,
        detail.submission.revision,
        json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False),
    )

    assert imported.submission.revision == 1
    assert imported.submission.label.persons[0].name_surface == "李白"


def test_enqueue_persists_job_without_persisting_api_key(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)

    payload = AIAnnotationGenerateRequest(
        task_id=task_id,
        config=AIAnnotationConfigRequest(provider="mock", api_key="temporary-secret"),
    )
    response = ExtractionAIAnnotationJobService(db).enqueue(payload, admin)

    saved = db.get(ExtractionAIAnnotationJob, response.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.task_id == task_id
    assert json.loads(saved.config_json)["api_key"] == ""
    assert ExtractionAIAnnotationJobService(db).list_latest_jobs(admin)[0].id == response.id


def test_enqueue_repair_persists_input_in_shared_queue(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    content = json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False)
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)

    task = db.get(ExtractionAnnotationTask, task_id)
    assert task is not None
    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=task.passage_id,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock-ai-annotation",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
    )
    db.add(root)
    db.commit()

    response = ExtractionAIAnnotationJobService(db).enqueue_repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content=content,
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-secret",
            ),
        ),
        admin,
    )

    saved = db.get(ExtractionAIAnnotationJob, response.id)
    assert saved is not None
    assert response.operation == "repair"
    assert saved.operation == "repair"
    assert saved.input_content == content
    assert saved.source_job_id == root.id
    assert json.loads(saved.config_json)["api_key"] == ""


def test_shared_queue_does_not_overlap_generation_and_repair(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)

    generation = ExtractionAIAnnotationJobService(db).enqueue(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        ),
        admin,
    )
    repair = ExtractionAIAnnotationJobService(db).enqueue_repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content="{}",
            config=AIAnnotationConfigRequest(provider="deepseek"),
        ),
        admin,
    )

    assert repair.id == generation.id
    assert repair.operation == "generate"
    assert db.query(ExtractionAIAnnotationJob).count() == 1


def test_background_worker_persists_success_result(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)
    local_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(job_module, "SessionLocal", local_session)

    payload = AIAnnotationGenerateRequest(
        task_id=task_id,
        config=AIAnnotationConfigRequest(provider="mock"),
    )
    response = ExtractionAIAnnotationJobService(db).enqueue(payload, admin)
    _run_job_background(response.id, payload)

    saved = db.get(ExtractionAIAnnotationJob, response.id)
    assert saved is not None
    assert saved.status == "success"
    assert saved.finished_at is not None
    assert saved.result_json is not None
    assert json.loads(saved.result_json)["validation_status"] == "valid"
    saved_result = db.query(ExtractionAIAnnotationSavedResult).one()
    assert saved_result.task_id == task_id
    assert json.loads(saved_result.result_json)["validation_status"] == "valid"
    listed = ExtractionAIAnnotationJobService(db).get_job(response.id, admin)
    assert listed.saved_result is not None
    assert listed.saved_result.validation_status == "valid"


def test_background_worker_runs_queued_repair(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    content = json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False)
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)
    local_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(job_module, "SessionLocal", local_session)
    monkeypatch.setattr(
        ExtractionAIAnnotationService,
        "repair",
        lambda self, payload: generated,
    )

    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=generated.passage_id,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock-ai-annotation",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
    )
    db.add(root)
    db.commit()
    payload = AIAnnotationRepairRequest(
        task_id=task_id,
        content=content,
        config=AIAnnotationConfigRequest(provider="deepseek"),
        job_id=root.id,
    )
    response = ExtractionAIAnnotationJobService(db).enqueue_repair(payload, admin)
    _run_job_background(response.id, payload)

    saved = db.get(ExtractionAIAnnotationJob, response.id)
    assert saved is not None
    assert saved.operation == "repair"
    assert saved.status == "success"
    saved_result = db.query(ExtractionAIAnnotationSavedResult).one()
    assert saved_result.source_job_id == response.id


def test_validation_persists_current_result_for_user(ai_annotation_db):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    generated = ExtractionAIAnnotationService(db).generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None

    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=generated.passage_id,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock-ai-annotation",
        result_json=json.dumps(generated.model_dump(mode="json"), ensure_ascii=False),
    )
    db.add(root)
    db.commit()

    result = ExtractionAIAnnotationService(db).validate(
        task_id,
        json.dumps(generated.document.model_dump(mode="json"), ensure_ascii=False),
        requested_by=admin.id,
        job_id=root.id,
    )

    saved_result = db.query(ExtractionAIAnnotationSavedResult).one()
    assert saved_result.source_job_id == root.id
    assert saved_result.lineage_job_id == root.id
    assert json.loads(saved_result.result_json)["source"] == "validated"
    assert result.validation_status == "valid"


def test_remote_generation_uses_bounded_semantic_fragments(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    calls = []
    fragment_by_purpose = {
        "extraction_ai_annotation_passage_person": {
            "passage": {"source_type": "epitaph"},
            "persons": [
                {
                    "key": "model-key",
                    "name_surface": "李白",
                    "level": 1,
                    "level_reason": "题名和正文均围绕该人物展开",
                    "mentions": [{"quote": "李白", "start": 0, "end": 2}],
                }
            ],
        },
        "extraction_ai_annotation_life_event_fragment": {
            "event_checks": {event_type: "not_mentioned" for event_type in ("出生", "籍贯", "死亡", "埋葬", "任职")},
            "life_events": [],
        },
        "extraction_ai_annotation_historical_event_fragment": {
            "historical_events": [],
        },
        "extraction_ai_annotation_relation_fragment": {
            "person_relations": [],
        },
    }

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, messages, metadata):
            calls.append((messages, metadata))
            return SimpleNamespace(
                content=json.dumps(fragment_by_purpose[metadata["purpose"]], ensure_ascii=False),
                model="fragment-model",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=4,
                    total_tokens=14,
                    prompt_cache_hit_tokens=3,
                    prompt_cache_miss_tokens=7,
                    cache_metrics_supported=True,
                ),
            )

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        FakeProvider,
    )

    result = service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert result.validation_status == "valid"
    assert len(calls) == 8
    assert calls[0][1]["purpose"] == "extraction_ai_annotation_passage_person"
    assert [call[1]["purpose"] for call in calls[1:6]] == [
        "extraction_ai_annotation_life_event_fragment"
    ] * 5
    assert calls[6][1]["purpose"] == "extraction_ai_annotation_historical_event_fragment"
    assert calls[7][1]["purpose"] == "extraction_ai_annotation_relation_fragment"
    assert all("max_tokens" not in call[1] and "thinking" not in call[1] for call in calls)
    assert result.fragment_count == 8
    assert result.truncated_fragments == []
    assert result.token_usage.prompt_tokens == 80
    assert result.token_usage.completion_tokens == 32
    assert result.token_usage.total_tokens == 112
    assert result.token_usage.prompt_cache_hit_tokens == 24
    assert result.token_usage.llm_call_count == 8
    assert result.document is not None
    assert result.document.label.persons[0].key == "p1"
    assert result.document.label.persons[0].mentions[0].id == "p1-m1"


def test_repair_rebuilds_only_semantic_fragments_and_persists(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    service = ExtractionAIAnnotationService(db)
    calls = []
    fragment_by_purpose = {
        "extraction_ai_annotation_passage_person": {
            "persons": [{
                "name_surface": "李白",
                "level": 1,
                "level_reason": "正文围绕该人物展开",
                "mentions": [{"quote": "李白", "start": 0, "end": 2}],
            }],
        },
        "extraction_ai_annotation_life_event_fragment": {
            "event_checks": {event_type: "not_mentioned" for event_type in ("出生", "籍贯", "死亡", "埋葬", "任职")},
        },
        "extraction_ai_annotation_historical_event_fragment": {},
        "extraction_ai_annotation_relation_fragment": {},
    }

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, messages, metadata):
            calls.append((messages, metadata))
            return SimpleNamespace(
                content=json.dumps(fragment_by_purpose[metadata["purpose"]], ensure_ascii=False),
                model="repair-model",
            )

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        FakeProvider,
    )

    root = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=1,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="mock",
        model="mock-ai-annotation",
        result_json="{}",
    )
    task = db.get(ExtractionAnnotationTask, task_id)
    assert task is not None
    root.passage_id = task.passage_id
    db.add(root)
    db.commit()

    result = service.repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            job_id=root.id,
            content="这不是 JSON",
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        ),
        requested_by=admin.id,
    )

    assert result.validation_status == "valid"
    assert len(calls) == 8
    assert all(call[1]["purpose"] != "extraction_ai_annotation_format_review" for call in calls)
    saved_result = db.query(ExtractionAIAnnotationSavedResult).one()
    assert saved_result.source_job_id == root.id
    assert saved_result.lineage_job_id == root.id
    assert json.loads(saved_result.result_json)["validation_status"] == "valid"


def test_fragment_length_finish_reason_retries_only_current_fragment(ai_annotation_db):
    db, _task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    calls = []

    class FakeProvider:
        def chat_completion_with_usage(self, _messages, metadata):
            calls.append(metadata)
            if len(calls) == 1:
                return SimpleNamespace(
                    content='{"partial":',
                    model="fragment-model",
                    raw_response={"choices": [{"finish_reason": "length"}]},
                )
            return SimpleNamespace(
                content="{}",
                model="fragment-model",
                raw_response={"choices": [{"finish_reason": "stop"}]},
            )

    fragment, attempts, _models, truncated, warnings = service._call_fragment_json(
        FakeProvider(),
        [{"role": "system", "content": "fragment"}, {"role": "user", "content": "{}"}],
        stage="测试分片",
        purpose="test_fragment",
    )

    assert fragment == {}
    assert attempts == 2
    assert all("max_tokens" not in call and "thinking" not in call for call in calls)
    assert truncated == ["测试分片"]
    assert warnings == []


def test_optional_empty_fragment_becomes_warning_instead_of_failing(ai_annotation_db):
    db, _task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    calls = []

    class EmptyProvider:
        def chat_completion_with_usage(self, _messages, metadata):
            calls.append(metadata)
            return SimpleNamespace(content="", model="fragment-model")

    fragment, attempts, _models, truncated, warnings = service._call_fragment_json(
        EmptyProvider(),
        [{"role": "system", "content": "fragment"}, {"role": "user", "content": "{}"}],
        stage="人物 p1 的埋葬事件分片",
        purpose="extraction_ai_annotation_life_event_fragment",
    )

    assert attempts == 3
    assert len(calls) == 3
    assert fragment["_fragment_warning"].endswith("empty_content")
    assert truncated == []
    assert len(warnings) == 1


def test_repair_routes_event_issue_to_one_event_fragment(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    generated = service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    document = generated.document.model_dump(mode="json")
    document["label"]["persons"][0]["event_checks"]["埋葬"] = "unreviewed"
    calls = []

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, messages, metadata):
            calls.append((messages, metadata))
            assert not service.db.in_transaction()
            assert metadata["purpose"] == "extraction_ai_annotation_life_event_repair"
            return SimpleNamespace(
                content=json.dumps(
                    {"event_check": "not_mentioned", "life_events": []},
                    ensure_ascii=False,
                ),
                model="targeted-repair-model",
            )

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        FakeProvider,
    )

    result = service.repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content=json.dumps(document, ensure_ascii=False),
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert result.validation_status == "valid"
    assert len(calls) == 1
    assert result.fragment_count == 1
    assert result.document is not None
    assert result.document.label.persons[0].event_checks["埋葬"] == "not_mentioned"


def test_repair_recomputes_existing_evidence_offsets(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    generated = service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    document = generated.document.model_dump(mode="json")
    mention = document["label"]["persons"][0]["mentions"][0]
    mention["start"] = 1
    mention["end"] = 3
    mention["start_utf16"] = 1
    mention["end_utf16"] = 3

    class NeverCalledProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, _messages, _metadata):
            raise AssertionError("evidence offset repair should not call the model")

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        NeverCalledProvider,
    )
    result = service.repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content=json.dumps(document, ensure_ascii=False),
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert result.validation_status == "valid"
    assert result.fragment_count == 0
    assert result.document is not None
    repaired_mention = result.document.label.persons[0].mentions[0]
    assert repaired_mention.start == 0
    assert repaired_mention.end == 2


def test_repair_drops_meaningless_excluded_mention_without_model_call(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    service = ExtractionAIAnnotationService(db)
    generated = service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    document = generated.document.model_dump(mode="json")
    document["label"]["excluded_mentions"] = [{"key": "x1", "reason": "", "evidence": []}]

    class NeverCalledProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, _messages, _metadata):
            raise AssertionError("meaningless excluded mentions must be dropped locally")

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        NeverCalledProvider,
    )
    result = service.repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content=json.dumps(document, ensure_ascii=False),
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert result.validation_status == "valid", result.validation_issues
    assert result.fragment_count == 0
    assert result.document is not None
    assert result.document.label.excluded_mentions == []


def test_repair_routes_relation_issue_to_relation_fragment(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    passage.context = "李白字太白，与故人交游。"
    task.context_sha256 = context_sha256(passage.context)
    db.commit()
    service = ExtractionAIAnnotationService(db)
    generated = service.generate(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(provider="mock"),
        )
    )
    assert generated.document is not None
    document = generated.document.model_dump(mode="json")
    first_person_key = document["label"]["persons"][0]["key"]
    second_person = json.loads(json.dumps(document["label"]["persons"][0], ensure_ascii=False))
    second_person.update(
        {
            "key": "p2",
            "name_surface": "太白",
            "level": 2,
            "mentions": [
                {
                    "id": "p2-m1",
                    "source": "context",
                    "quote": "太白",
                    "start": 3,
                    "end": 5,
                    "start_utf16": 3,
                    "end_utf16": 5,
                }
            ],
        }
    )
    document["label"]["persons"].append(second_person)
    third_person = json.loads(json.dumps(second_person, ensure_ascii=False))
    third_person.update(
        {
            "key": "p3",
            "name_surface": "故人",
            "level": 3,
            "mentions": [{"id": "p3-m1", "source": "context", "quote": "故人", "start": 7, "end": 9, "start_utf16": 7, "end_utf16": 9}],
        }
    )
    document["label"]["persons"].append(third_person)
    document["label"]["person_relations"] = [
        {
            "key": "r1",
            "source_person_key": "p2",
            "target_person_key": first_person_key,
            "relation_category": "",
            "relation_summary": "",
            "state": "confirmed",
            "evidence": [],
        }
    ]

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def chat_completion_with_usage(self, _messages, metadata):
            assert metadata["purpose"] == "extraction_ai_annotation_relation_repair"
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "person_relations": [
                            {
                                "source_person_key": first_person_key,
                                "target_person_key": "p3",
                                "relation_category": "交游与往来",
                                "relation_summary": "交游",
                                "state": "confirmed",
                                "evidence": [{"quote": "与故人交游", "start": 6, "end": 11}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                model="targeted-repair-model",
            )

    monkeypatch.setattr(
        "app.services.extraction_ai_annotation_service.DeepSeekProvider",
        FakeProvider,
    )
    result = service.repair(
        AIAnnotationRepairRequest(
            task_id=task_id,
            content=json.dumps(document, ensure_ascii=False),
            config=AIAnnotationConfigRequest(
                provider="deepseek",
                api_key="temporary-key",
                base_url="https://example.test",
                model="annotation-model",
            ),
        )
    )

    assert result.validation_status == "valid", result.validation_issues
    assert result.fragment_count == 1
    assert result.document is not None
    assert len(result.document.label.person_relations) == 1
    assert result.document.label.person_relations[0].target_person_key == "p3"
    assert result.document.label.person_relations[0].relation_category == "交游与往来"


def _person_payload(mentions: list[dict]) -> dict:
    return {
        "key": "p4",
        "name_surface": "刘季述",
        "level": 2,
        "level_reason": "测试占用区间",
        "mentions": mentions,
        "event_checks": {event_type: "not_mentioned" for event_type in EVENT_TYPES},
    }


def test_same_person_mentions_match_longest_first_and_skip_occupied_spans(ai_annotation_db):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    assert task is not None and passage is not None
    passage.context = "遇刘季述，明日季述等，授季述。"
    full_start = passage.context.find("刘季述")
    nested_start = full_start + 1
    later_start = passage.context.find("季述", full_start + 3)
    service = ExtractionAIAnnotationService(db)

    label = service._normalize_assembled_label(
        task,
        passage,
        {
            "schema_version": task.spec_version,
            "passage": {},
            "persons": [
                _person_payload(
                    [
                        {
                            "quote": "季述",
                            "start": nested_start,
                            "end": nested_start + 2,
                        },
                        {
                            "quote": "刘季述",
                            "start": full_start,
                            "end": full_start + 3,
                        },
                    ]
                )
            ],
        },
    )

    mentions = [(item.quote, item.start, item.end) for item in label.persons[0].mentions]
    assert mentions == [
        ("刘季述", full_start, full_start + 3),
        ("季述", later_start, later_start + 2),
    ]
    assert later_start == passage.context.find("明日季述") + 2


def test_same_person_drops_short_mention_when_only_occupied_span_remains(ai_annotation_db):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    assert task is not None and passage is not None
    passage.context = "遇刘季述，留王紫廷院。"
    full_start = passage.context.find("刘季述")
    service = ExtractionAIAnnotationService(db)

    label = service._normalize_assembled_label(
        task,
        passage,
        {
            "schema_version": task.spec_version,
            "passage": {},
            "persons": [
                _person_payload(
                    [
                        {"quote": "刘季述", "start": full_start, "end": full_start + 3},
                        {"quote": "季述", "start": full_start + 1, "end": full_start + 3},
                    ]
                )
            ],
        },
    )

    mentions = [(item.quote, item.start, item.end) for item in label.persons[0].mentions]
    assert mentions == [("刘季述", full_start, full_start + 3)]


def test_overlong_label_fields_keep_original_text_and_mark_schema_mismatch(ai_annotation_db):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    assert task is not None and passage is not None
    admin = db.get(User, 1)
    assert admin is not None
    service = ExtractionAIAnnotationService(db)
    original_summary = "请帝劳军牵动后从御南楼"
    historical_summary = "请帝劳军牵动后从御南楼观礼毕还宫"
    titles = [f"衔{index}" for index in range(21)]
    person = _person_payload([])
    person["titles"] = titles
    person["historical_events"] = [
        {
            "event_name": "劳军",
            "relation_summary": historical_summary,
            "state": "confirmed",
            "evidence": [],
        }
    ]

    label = service._normalize_assembled_label(
        task,
        passage,
        {
            "schema_version": task.spec_version,
            "passage": {},
            "persons": [person],
            "person_relations": [
                {
                    "source_person_key": "p1",
                    "target_person_key": "p2",
                    "relation_category": "隶属与任用",
                    "relation_summary": original_summary,
                    "state": "confirmed",
                    "evidence": [],
                },
                {
                    "source_person_key": "p1",
                    "target_person_key": "p3",
                    "relation_category": "亲属",
                    "relation_summary": "父子",
                    "state": "confirmed",
                    "evidence": [],
                },
            ],
        },
    )

    assert label.person_relations[0].relation_summary == original_summary
    assert label.person_relations[1].relation_summary == "父子"
    assert label.persons[0].historical_events[0].relation_summary == historical_summary
    assert label.persons[0].titles == titles
    result = service._validate_content(
        task,
        passage,
        json.dumps(label.model_dump(mode="json"), ensure_ascii=False),
        source="generated",
        provider="百炼",
        model="qwen3.8-max",
        elapsed_ms=0,
    )
    mismatched = {
        issue.path: issue
        for issue in result.validation_issues
        if issue.code == "schema_mismatch"
    }
    assert result.document is not None
    assert result.document.label.person_relations[0].relation_summary == original_summary
    assert result.validation_status == "invalid_rules"
    assert mismatched["person_relations.0.relation_summary"].message == (
        "与 schema 不符：最多 10 个字符，当前 11 个，原文已保留。"
    )
    assert "person_relations.1.relation_summary" not in mismatched
    assert "最多 15 个字符，当前 16 个" in mismatched["persons.0.historical_events.0.relation_summary"].message
    assert mismatched["persons.0.titles"].message == (
        "与 schema 不符：最多 20 条，当前 21 条，原列表已保留。"
    )
    job = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=task.passage_id,
        requested_by=admin.id,
        operation="generate",
        status="success",
        provider="百炼",
        model="qwen3.8-max",
        result_json=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    loaded = parse_ai_annotation_result(json.loads(job.result_json or "{}"))
    assert loaded.document is not None
    assert loaded.document.label.person_relations[0].relation_summary == original_summary
    assert loaded.document.label.persons[0].titles == titles
    assert "model_output_json" not in AIAnnotationJobResponse.model_fields


def test_non_length_schema_error_still_fails_assembly(ai_annotation_db):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    assert task is not None and passage is not None
    service = ExtractionAIAnnotationService(db)

    with pytest.raises(ValidationError):
        service._normalize_assembled_label(
            task,
            passage,
            {
                "schema_version": task.spec_version,
                "passage": {},
                "persons": [],
                "person_relations": [
                    {
                        "source_person_key": "p1",
                        "target_person_key": "p2",
                        "relation_category": "朋友",
                        "relation_summary": "请帝劳军牵动后从御南楼",
                        "state": "confirmed",
                        "evidence": [],
                    }
                ],
            },
        )


def test_model_output_is_kept_when_later_schema_validation_fails(ai_annotation_db):
    db, task_id = ai_annotation_db
    task = db.get(ExtractionAnnotationTask, task_id)
    passage = db.get(Passage, task.passage_id)
    admin = db.get(User, 1)
    assert task is not None and passage is not None and admin is not None
    job = ExtractionAIAnnotationJob(
        task_id=task_id,
        passage_id=task.passage_id,
        requested_by=admin.id,
        operation="generate",
        status="running",
        provider="百炼",
        model="qwen3.8-max",
    )
    db.add(job)
    db.commit()
    service = ExtractionAIAnnotationService(db)
    service.capture_model_output(job.id)
    service._remember_model_output("人物关系分片 1", "请帝劳军牵动后从御南楼")

    with pytest.raises(ValidationError):
        service._normalize_assembled_label(
            task,
            passage,
            {
                "schema_version": task.spec_version,
                "passage": {},
                "persons": [],
                "person_relations": [
                    {
                        "source_person_key": "p1",
                        "target_person_key": "p2",
                        "relation_category": "朋友",
                        "relation_summary": "请帝劳军牵动后从御南楼",
                        "state": "confirmed",
                        "evidence": [],
                    }
                ],
            },
        )

    db.rollback()
    stored = db.get(ExtractionAIAnnotationJob, job.id)
    assert stored is not None
    saved = json.loads(stored.model_output_json or "{}")
    stages = [item["stage"] for item in saved["fragments"]]
    assert stages == ["人物关系分片 1", "assembled"]
    assert saved["fragments"][0]["content"] == "请帝劳军牵动后从御南楼"
    assert saved["fragments"][1]["content"]["person_relations"][0]["relation_summary"] == "请帝劳军牵动后从御南楼"


def test_custom_provider_name_is_kept_for_remote_calls():
    provider_name, model_name = ExtractionAIAnnotationService._resolve_provider_config(
        AIAnnotationConfigRequest(
            provider="硅基流动",
            base_url="https://api.example.com/v1",
            model="Qwen2.5",
        )
    )

    assert provider_name == "硅基流动"
    assert model_name == "Qwen2.5"


def test_builtin_provider_names_are_normalized():
    deepseek_name, _model_name = ExtractionAIAnnotationService._resolve_provider_config(
        AIAnnotationConfigRequest(provider="DeepSeek", model="deepseek-chat")
    )
    compatible_name, _compatible_model = ExtractionAIAnnotationService._resolve_provider_config(
        AIAnnotationConfigRequest(provider="OpenAI-Compatible", model="gpt-4o")
    )

    assert deepseek_name == "deepseek"
    assert compatible_name == "openai_compatible"


def test_provider_name_rejects_control_characters_and_overlong_values():
    with pytest.raises(AIAnnotationConfigError, match="控制字符"):
        ExtractionAIAnnotationService._resolve_provider_config(
            AIAnnotationConfigRequest(provider="硅基\n流动", model="Qwen2.5")
        )

    with pytest.raises(ValidationError):
        AIAnnotationConfigRequest(provider="名" * 65, model="Qwen2.5")


def test_enqueue_stores_custom_provider_without_api_key(ai_annotation_db, monkeypatch):
    db, task_id = ai_annotation_db
    admin = db.get(User, 1)
    assert admin is not None
    monkeypatch.setattr(job_module, "_submit_job", lambda _job_id, _payload: None)

    response = ExtractionAIAnnotationJobService(db).enqueue(
        AIAnnotationGenerateRequest(
            task_id=task_id,
            config=AIAnnotationConfigRequest(
                provider="硅基流动",
                base_url="https://api.example.com/v1",
                model="Qwen2.5",
                api_key="temporary-secret",
            ),
        ),
        admin,
    )

    saved = db.get(ExtractionAIAnnotationJob, response.id)
    assert saved is not None
    assert saved.provider == "硅基流动"
    assert saved.model == "Qwen2.5"
    stored = json.loads(saved.config_json)
    assert stored["provider"] == "硅基流动"
    assert stored["api_key"] == ""
