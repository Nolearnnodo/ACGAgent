from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.observability import (
    ExecutionTraceSummary,
    LLMCallLog,
    ModelPricingRule,
    ToolCallLog,
)
from app.observability.costing import estimate_cost
from app.observability.schemas import LLMCallResult, LLMUsage

_MAX_JSON_CHARS = 12000
_MAX_TEXT_CHARS = 12000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        text = json.dumps(str(payload), ensure_ascii=False)
    if len(text) > _MAX_JSON_CHARS:
        text = text[:_MAX_JSON_CHARS] + f"[...truncated, original {len(text)} chars]"
    return text


def _clip_text(text: str) -> str:
    if len(text) > _MAX_TEXT_CHARS:
        return text[:_MAX_TEXT_CHARS] + f"[...truncated, original {len(text)} chars]"
    return text


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _lookup_pricing(db: Session, provider: str, model: str) -> ModelPricingRule | None:
    now = _utcnow()
    base = (
        db.query(ModelPricingRule)
        .filter(ModelPricingRule.provider == provider)
        .filter(ModelPricingRule.effective_from <= now)
        .filter((ModelPricingRule.effective_to.is_(None)) | (ModelPricingRule.effective_to > now))
    )
    exact = base.filter(ModelPricingRule.model == model).order_by(ModelPricingRule.effective_from.desc()).first()
    if exact is not None:
        return exact
    return (
        base.filter(ModelPricingRule.model.ilike(f"{model}%") | ModelPricingRule.model.ilike(f"%{model}%"))
        .order_by(ModelPricingRule.effective_from.desc())
        .first()
    )


def _empty_cost(usage: LLMUsage):
    return estimate_cost(
        usage=usage,
        cache_hit_input_price_per_1m=0.0,
        cache_miss_input_price_per_1m=0.0,
        output_price_per_1m=0.0,
        currency="CNY",
    )


def record_llm_call(
    db: Session,
    trace_context: dict[str, Any],
    messages: list[dict[str, str]],
    result: LLMCallResult,
    status: str,
    error_message: str = "",
) -> LLMCallLog:
    pricing = _lookup_pricing(db, result.provider, result.model)
    cost = (
        estimate_cost(
            usage=result.usage,
            cache_hit_input_price_per_1m=pricing.cache_hit_input_price_per_1m,
            cache_miss_input_price_per_1m=pricing.cache_miss_input_price_per_1m,
            output_price_per_1m=pricing.output_price_per_1m,
            currency=pricing.currency,
        )
        if pricing is not None
        else _empty_cost(result.usage)
    )
    log = LLMCallLog(
        execution_run_id=_int_or_none(trace_context.get("execution_run_id")),
        execution_step_run_id=_int_or_none(trace_context.get("execution_step_run_id")),
        conversation_id=_int_or_none(trace_context.get("conversation_id")),
        message_id=_int_or_none(trace_context.get("message_id")),
        passage_id=_int_or_none(trace_context.get("passage_id")),
        skill_code=str(trace_context.get("skill_code") or ""),
        call_purpose=str(trace_context.get("purpose") or trace_context.get("call_purpose") or ""),
        provider=result.provider,
        model=result.model,
        request_json=_json_dumps({"messages": messages}),
        response_json=_json_dumps(result.raw_response),
        response_text=_clip_text(result.content or ""),
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        total_tokens=result.usage.total_tokens,
        prompt_cache_hit_tokens=result.usage.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=result.usage.prompt_cache_miss_tokens or 0,
        cache_hit_ratio=result.usage.cache_hit_ratio,
        cache_metrics_supported=result.usage.cache_metrics_supported,
        estimated_input_cost=cost.input_cost,
        estimated_output_cost=cost.output_cost,
        estimated_total_cost=cost.total_cost,
        currency=cost.currency,
        latency_ms=result.latency_ms,
        retry_count=result.retry_count,
        status=status,
        error_message=_clip_text(error_message or ""),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    if log.execution_run_id is not None:
        refresh_execution_summary(db, log.execution_run_id)
    return log


def refresh_execution_summary(db: Session, execution_run_id: int) -> ExecutionTraceSummary:
    llm_totals = (
        db.query(
            func.count(LLMCallLog.id),
            func.coalesce(func.sum(LLMCallLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.total_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.prompt_cache_hit_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.prompt_cache_miss_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.estimated_input_cost), 0.0),
            func.coalesce(func.sum(LLMCallLog.estimated_output_cost), 0.0),
            func.coalesce(func.sum(LLMCallLog.estimated_total_cost), 0.0),
            func.coalesce(func.sum(LLMCallLog.latency_ms), 0),
            func.coalesce(func.sum(case((LLMCallLog.status == "failed", 1), else_=0)), 0),
        )
        .filter(LLMCallLog.execution_run_id == execution_run_id)
        .one()
    )
    tool_totals = (
        db.query(
            func.count(ToolCallLog.id),
            func.coalesce(func.sum(ToolCallLog.latency_ms), 0),
            func.coalesce(func.sum(case((ToolCallLog.status == "failed", 1), else_=0)), 0),
        )
        .filter(ToolCallLog.execution_run_id == execution_run_id)
        .one()
    )
    (
        llm_call_count,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        hit_tokens,
        miss_tokens,
        input_cost,
        output_cost,
        total_cost,
        llm_latency_ms,
        failed_llm_count,
    ) = llm_totals
    tool_call_count, tool_latency_ms, failed_tool_count = tool_totals
    cache_hit_ratio = float(hit_tokens) / float(prompt_tokens) if prompt_tokens else 0.0

    summary = (
        db.query(ExecutionTraceSummary)
        .filter(ExecutionTraceSummary.execution_run_id == execution_run_id)
        .first()
    )
    if summary is None:
        summary = ExecutionTraceSummary(execution_run_id=execution_run_id)
    summary.llm_call_count = int(llm_call_count or 0)
    summary.tool_call_count = int(tool_call_count or 0)
    summary.prompt_tokens = int(prompt_tokens or 0)
    summary.completion_tokens = int(completion_tokens or 0)
    summary.total_tokens = int(total_tokens or 0)
    summary.prompt_cache_hit_tokens = int(hit_tokens or 0)
    summary.prompt_cache_miss_tokens = int(miss_tokens or 0)
    summary.cache_hit_ratio = cache_hit_ratio
    summary.estimated_input_cost = float(input_cost or 0.0)
    summary.estimated_output_cost = float(output_cost or 0.0)
    summary.estimated_total_cost = float(total_cost or 0.0)
    summary.currency = "CNY"
    summary.total_latency_ms = int(llm_latency_ms or 0) + int(tool_latency_ms or 0)
    summary.failed_call_count = int(failed_llm_count or 0) + int(failed_tool_count or 0)
    summary.updated_at = _utcnow()
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return summary
