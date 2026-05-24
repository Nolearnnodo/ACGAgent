from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import base  # noqa: F401
from app.db.base_class import Base
from app.llm.base import BaseLLMProvider
from app.models.execution import ExecutionRun
from app.models.observability import ExecutionTraceSummary, LLMCallLog
from app.observability.llm_tracer import traced_chat_completion
from app.observability.schemas import LLMCallResult, LLMUsage


class FakeProvider(BaseLLMProvider):
    def generate_structured_intent(self, prompt, metadata):
        return {}

    def chat_completion(self, messages, metadata):
        return "ok"

    def chat_completion_with_usage(self, messages, metadata):
        return LLMCallResult(
            content='{"ok": true}',
            usage=LLMUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                prompt_cache_hit_tokens=70,
                prompt_cache_miss_tokens=30,
                cache_metrics_supported=True,
            ),
            provider="fake",
            model="fake-model",
            raw_response={"usage": {"prompt_tokens": 100}},
            latency_ms=12,
        )


def test_traced_chat_completion_persists_llm_log_and_summary():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        run = ExecutionRun(trigger_type="passage_upload", status="running", passage_id=123)
        db.add(run)
        db.commit()
        db.refresh(run)

        content = traced_chat_completion(
            db=db,
            provider=FakeProvider(),
            messages=[{"role": "user", "content": "hello"}],
            metadata={"skill_code": "probe_atomic"},
            trace_context={
                "execution_run_id": run.id,
                "passage_id": 123,
                "skill_code": "probe_atomic",
                "call_purpose": "test",
            },
        )

        assert content == '{"ok": true}'
        log = db.query(LLMCallLog).one()
        assert log.execution_run_id == run.id
        assert log.passage_id == 123
        assert log.prompt_cache_hit_tokens == 70
        assert log.prompt_cache_miss_tokens == 30
        assert log.cache_hit_ratio == 0.7

        summary = db.query(ExecutionTraceSummary).one()
        assert summary.execution_run_id == run.id
        assert summary.llm_call_count == 1
        assert summary.prompt_tokens == 100
        assert summary.prompt_cache_hit_tokens == 70
        assert summary.cache_hit_ratio == 0.7
