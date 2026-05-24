from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.llm.base import BaseLLMProvider
from app.observability.schemas import LLMCallResult, LLMUsage
from app.observability.trace_repository import record_llm_call

logger = logging.getLogger(__name__)


def _should_trace(trace_context: dict[str, Any]) -> bool:
    return bool(trace_context.get("execution_run_id"))


def _failure_result(provider: BaseLLMProvider, error_message: str) -> LLMCallResult:
    provider_name = provider.__class__.__name__.replace("Provider", "").lower() or "unknown"
    model = getattr(getattr(provider, "settings", None), "llm_model_name", "unknown")
    return LLMCallResult(
        content="",
        usage=LLMUsage(),
        provider=provider_name,
        model=str(model or "unknown"),
        raw_response={},
        latency_ms=0,
    )


def _record_safely(
    db: Session,
    trace_context: dict[str, Any],
    messages: list[dict[str, str]],
    result: LLMCallResult,
    status: str,
    error_message: str = "",
) -> None:
    try:
        record_llm_call(
            db=db,
            trace_context=trace_context,
            messages=messages,
            result=result,
            status=status,
            error_message=error_message,
        )
    except Exception:
        logger.exception("Failed to persist LLM trace; continuing without trace.")


def traced_chat_completion(
    provider: BaseLLMProvider,
    messages: list[dict[str, str]],
    metadata: dict[str, Any],
    trace_context: dict[str, Any] | None = None,
    db: Session | None = None,
) -> str:
    trace_context = trace_context or {}
    if not _should_trace(trace_context):
        return provider.chat_completion(messages, metadata)

    try:
        result = provider.chat_completion_with_usage(messages, metadata)
    except Exception as exc:
        if db is not None:
            _record_safely(
                db=db,
                trace_context=trace_context,
                messages=messages,
                result=_failure_result(provider, str(exc)),
                status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        else:
            with SessionLocal() as trace_db:
                _record_safely(
                    db=trace_db,
                    trace_context=trace_context,
                    messages=messages,
                    result=_failure_result(provider, str(exc)),
                    status="failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
        raise

    if db is not None:
        _record_safely(
            db=db,
            trace_context=trace_context,
            messages=messages,
            result=result,
            status="success",
        )
    else:
        with SessionLocal() as trace_db:
            _record_safely(
                db=trace_db,
                trace_context=trace_context,
                messages=messages,
                result=result,
                status="success",
            )
    return result.content
