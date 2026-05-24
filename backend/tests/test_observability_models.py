from typing import get_args

from app.models.observability import ExecutionTraceSummary, LLMCallLog, ModelPricingRule, ToolCallLog
from app.models.passage import Passage


def test_observability_models_have_expected_tables():
    assert LLMCallLog.__tablename__ == "llm_call_logs"
    assert ToolCallLog.__tablename__ == "tool_call_logs"
    assert ExecutionTraceSummary.__tablename__ == "execution_trace_summaries"
    assert ModelPricingRule.__tablename__ == "model_pricing_rules"


def test_llm_call_log_passage_id_matches_passage_doc_id_type():
    assert str(Passage.__table__.c.doc_id.type) == str(LLMCallLog.__table__.c.passage_id.type)
    assert get_args(LLMCallLog.__annotations__["passage_id"])[0] == int | None
    assert LLMCallLog.__table__.c.passage_id.nullable is True
