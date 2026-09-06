"""AI 标注实验指标聚合服务。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.extraction_ai_annotation import ExtractionAIAnnotationJob
from app.models.extraction_annotation import ExtractionAnnotationSubmission
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationDailyMetric,
    AIAnnotationMetricDifference,
    AIAnnotationMetricError,
    AIAnnotationMetricsRecord,
    AIAnnotationMetricsResponse,
    AIAnnotationMetricsSummary,
    AIAnnotationTokenUsage,
    AIAnnotationValidationIssue,
    ExtractionAnnotationLabel,
)


_DIFFERENCE_LABELS = {
    "passage": "文献字段",
    "person": "人物信息",
    "relation": "人物关系",
    "review": "复核与排除项",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    start_utc = _utc(start)
    end_utc = _utc(end)
    if start_utc is None or end_utc is None:
        return None
    return max(int((end_utc - start_utc).total_seconds() * 1000), 0)


def _job_total_tokens(job: ExtractionAIAnnotationJob) -> int:
    """兼容未返回 total_tokens、但已记录输入/输出用量的历史 Provider。"""

    prompt_tokens = max(int(job.prompt_tokens or 0), 0)
    completion_tokens = max(int(job.completion_tokens or 0), 0)
    return max(int(job.total_tokens or 0), prompt_tokens + completion_tokens, 0)


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _root_for(
    job: ExtractionAIAnnotationJob,
    jobs_by_id: dict[int, ExtractionAIAnnotationJob],
    cache: dict[int, ExtractionAIAnnotationJob],
) -> ExtractionAIAnnotationJob:
    cached = cache.get(job.id)
    if cached is not None:
        return cached

    current = job
    trail = [current.id]
    visited = {current.id}
    while current.operation == "repair" and current.source_job_id is not None:
        parent = jobs_by_id.get(current.source_job_id)
        if parent is None or parent.id in visited:
            break
        current = parent
        visited.add(parent.id)
        trail.append(parent.id)
    for job_id in trail:
        cache[job_id] = current
    return current


def _first_round_errors(job: ExtractionAIAnnotationJob) -> list[AIAnnotationValidationIssue]:
    raw_items = _json_list(job.first_round_errors_json)
    if not raw_items:
        result = _json_object(job.result_json)
        candidate = result.get("validation_issues")
        raw_items = candidate if isinstance(candidate, list) else []
    issues: list[AIAnnotationValidationIssue] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            issues.append(AIAnnotationValidationIssue.model_validate(item))
        except (TypeError, ValueError):
            continue
    if not issues and job.status == "failed":
        issues.append(
            AIAnnotationValidationIssue(
                path="root",
                message=job.error_message or "AI 第一轮标注失败",
                code="generation_failed",
            )
        )
    return issues


def _result_diagnostics(job: ExtractionAIAnnotationJob) -> tuple[str, int, int, int | None]:
    result = _json_object(job.result_json)
    validation_status = str(
        job.first_round_validation_status or result.get("validation_status") or ""
    )
    warnings = result.get("fragment_warnings")
    truncated = result.get("truncated_fragments")
    elapsed = result.get("elapsed_ms")
    return (
        validation_status,
        max(
            int(job.first_round_warning_count or 0),
            len(warnings) if isinstance(warnings, list) else 0,
        ),
        max(
            int(job.first_round_truncated_count or 0),
            len(truncated) if isinstance(truncated, list) else 0,
        ),
        int(elapsed) if isinstance(elapsed, (int, float)) and elapsed >= 0 else None,
    )


def _final_differences(
    root: ExtractionAIAnnotationJob,
    submission: ExtractionAnnotationSubmission | None,
) -> tuple[int, dict[str, int]]:
    stored = {
        str(key): max(int(value or 0), 0)
        for key, value in _json_object(root.final_differences_json).items()
        if isinstance(value, (int, float))
    }
    if submission is None:
        return max(int(root.final_difference_count or 0), 0), stored

    try:
        result = _json_object(root.result_json)
        first_payload = result.get("label")
        if not isinstance(first_payload, dict):
            document = result.get("document")
            first_payload = document.get("label") if isinstance(document, dict) else None
        if not isinstance(first_payload, dict):
            return max(int(root.final_difference_count or 0), 0), stored

        first_label = ExtractionAnnotationLabel.model_validate(first_payload)
        final_label = ExtractionAnnotationLabel.model_validate(
            _json_object(submission.label_json)
        )
        from app.services.extraction_adjudication_service import (
            build_adjudication_differences,
        )

        differences = build_adjudication_differences(first_label, final_label)
        counts = Counter(item.category for item in differences)
        return len(differences), dict(counts)
    except Exception:
        return max(int(root.final_difference_count or 0), 0), stored


class ExtractionAIAnnotationMetricsService:
    """按一次“生成”请求聚合其后续修复与最终人工提交。"""

    def __init__(self, db: Session):
        self.db = db

    def get_metrics(
        self,
        *,
        days: int = 30,
        limit: int = 500,
    ) -> AIAnnotationMetricsResponse:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days) if days > 0 else None
        jobs = (
            self.db.query(ExtractionAIAnnotationJob)
            .order_by(ExtractionAIAnnotationJob.created_at.asc(), ExtractionAIAnnotationJob.id.asc())
            .all()
        )
        jobs_by_id = {job.id: job for job in jobs}
        root_cache: dict[int, ExtractionAIAnnotationJob] = {}
        jobs_by_root: dict[int, list[ExtractionAIAnnotationJob]] = defaultdict(list)
        for job in jobs:
            root = _root_for(job, jobs_by_id, root_cache)
            jobs_by_root[root.id].append(job)

        roots = [
            job
            for job in jobs
            if job.operation == "generate"
            and (cutoff is None or (_utc(job.client_started_at or job.created_at) or now) >= cutoff)
        ]
        roots.sort(key=lambda item: (_utc(item.client_started_at or item.created_at) or now), reverse=True)

        passages = {
            passage.doc_id: passage
            for passage in self.db.query(Passage)
            .filter(Passage.doc_id.in_({root.passage_id for root in roots}))
            .all()
        } if roots else {}
        users = {
            user.id: user
            for user in self.db.query(User)
            .filter(User.id.in_({root.requested_by for root in roots}))
            .all()
        } if roots else {}

        submitted_by_root: dict[int, ExtractionAnnotationSubmission] = {}
        submissions = (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(
                ExtractionAnnotationSubmission.source_ai_job_id.is_not(None),
                ExtractionAnnotationSubmission.state == "submitted",
            )
            .all()
        )
        for submission in submissions:
            source = jobs_by_id.get(submission.source_ai_job_id or 0)
            if source is None:
                continue
            root = _root_for(source, jobs_by_id, root_cache)
            previous = submitted_by_root.get(root.id)
            if previous is None or (_utc(submission.submitted_at) or now) > (
                _utc(previous.submitted_at) or datetime.min.replace(tzinfo=timezone.utc)
            ):
                submitted_by_root[root.id] = submission

        records: list[AIAnnotationMetricsRecord] = []
        validation_counts: Counter[str] = Counter()
        validation_sessions: dict[str, set[int]] = defaultdict(set)
        validation_examples: dict[str, str] = {}
        difference_counts: Counter[str] = Counter()
        daily_values: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "session_count": 0,
                "submitted_count": 0,
                "total_tokens": 0,
                "first_round_error_count": 0,
            }
        )

        for root in roots:
            session_jobs = jobs_by_root.get(root.id, [root])
            session_jobs.sort(key=lambda item: (item.created_at, item.id))
            submission = submitted_by_root.get(root.id)
            submitted_at = root.submitted_at or (
                submission.submitted_at if submission is not None else None
            )
            submitted_at_utc = _utc(submitted_at)
            measured_jobs = [
                item
                for item in session_jobs
                if submitted_at_utc is None
                or (_utc(item.created_at) or submitted_at_utc) <= submitted_at_utc
            ] or [root]
            latest = measured_jobs[-1]
            usage = AIAnnotationTokenUsage(
                prompt_tokens=sum(max(int(item.prompt_tokens or 0), 0) for item in measured_jobs),
                completion_tokens=sum(max(int(item.completion_tokens or 0), 0) for item in measured_jobs),
                total_tokens=sum(_job_total_tokens(item) for item in measured_jobs),
                prompt_cache_hit_tokens=sum(
                    max(int(item.prompt_cache_hit_tokens or 0), 0) for item in measured_jobs
                ),
                prompt_cache_miss_tokens=sum(
                    max(int(item.prompt_cache_miss_tokens or 0), 0) for item in measured_jobs
                ),
                cache_metrics_supported=any(
                    bool(item.cache_metrics_supported) for item in measured_jobs
                ),
                llm_call_count=sum(max(int(item.llm_call_count or 0), 0) for item in measured_jobs),
            )
            errors = _first_round_errors(root)
            validation_status, warning_count, truncated_count, result_elapsed = _result_diagnostics(root)
            for issue in errors:
                validation_counts[issue.code] += 1
                validation_sessions[issue.code].add(root.id)
                validation_examples.setdefault(issue.code, issue.message)

            final_count, final_categories = _final_differences(root, submission)
            difference_counts.update(final_categories)
            clicked_at = _utc(root.client_started_at or root.created_at) or now
            first_round_elapsed = _duration_ms(root.started_at, root.finished_at)
            if first_round_elapsed is None:
                first_round_elapsed = result_elapsed
            passage = passages.get(root.passage_id)
            user = users.get(root.requested_by)
            record = AIAnnotationMetricsRecord(
                session_id=root.id,
                task_id=root.task_id,
                passage_id=root.passage_id,
                passage_title=passage.title if passage is not None else f"篇目 #{root.passage_id}",
                requested_by=root.requested_by,
                requested_by_email=user.email if user is not None else f"用户 #{root.requested_by}",
                provider=root.provider,
                model=root.model,
                status=latest.status,
                first_round_validation_status=validation_status,
                client_started_at=clicked_at,
                queued_at=_utc(root.created_at) or clicked_at,
                started_at=_utc(root.started_at),
                first_round_finished_at=_utc(root.finished_at),
                submitted_at=_utc(submitted_at),
                queue_wait_ms=_duration_ms(clicked_at, root.started_at),
                first_round_elapsed_ms=first_round_elapsed,
                end_to_end_ms=_duration_ms(clicked_at, submitted_at),
                token_usage=usage,
                repair_rounds=sum(item.operation == "repair" for item in measured_jobs),
                first_round_errors=errors,
                first_round_warning_count=warning_count,
                first_round_truncated_count=truncated_count,
                final_difference_count=final_count,
                final_difference_categories=final_categories,
                submission_id=submission.id if submission is not None else None,
            )
            records.append(record)

            day = clicked_at.date().isoformat()
            daily_values[day]["session_count"] += 1
            daily_values[day]["submitted_count"] += int(record.submitted_at is not None)
            daily_values[day]["total_tokens"] += usage.total_tokens
            daily_values[day]["first_round_error_count"] += len(errors)

        completed = [
            item
            for item in records
            if item.first_round_validation_status in {"valid", "invalid_format", "invalid_rules"}
            or (
                item.first_round_finished_at is not None
                and item.first_round_validation_status != "failed"
            )
        ]
        failed_first_round_count = sum(
            item.first_round_validation_status == "failed"
            or (
                item.first_round_finished_at is None
                and item.status == "failed"
            )
            for item in records
        )
        first_round_durations = [
            item.first_round_elapsed_ms
            for item in records
            if item.first_round_elapsed_ms is not None
        ]
        end_to_end_durations = [
            item.end_to_end_ms for item in records if item.end_to_end_ms is not None
        ]
        submitted_count = sum(item.submitted_at is not None for item in records)
        first_pass_valid_count = sum(
            item.first_round_validation_status == "valid" for item in records
        )
        session_count = len(records)
        completed_count = len(completed)
        summary = AIAnnotationMetricsSummary(
            session_count=session_count,
            completed_first_round_count=completed_count,
            failed_first_round_count=failed_first_round_count,
            first_pass_valid_count=first_pass_valid_count,
            first_pass_valid_rate=(first_pass_valid_count / completed_count if completed_count else 0),
            submitted_count=submitted_count,
            submission_rate=(submitted_count / session_count if session_count else 0),
            average_first_round_ms=(
                round(sum(first_round_durations) / len(first_round_durations))
                if first_round_durations
                else 0
            ),
            average_end_to_end_ms=(
                round(sum(end_to_end_durations) / len(end_to_end_durations))
                if end_to_end_durations
                else 0
            ),
            total_prompt_tokens=sum(item.token_usage.prompt_tokens for item in records),
            total_completion_tokens=sum(item.token_usage.completion_tokens for item in records),
            total_tokens=sum(item.token_usage.total_tokens for item in records),
            total_cache_hit_tokens=sum(
                item.token_usage.prompt_cache_hit_tokens for item in records
            ),
            total_cache_miss_tokens=sum(
                item.token_usage.prompt_cache_miss_tokens for item in records
            ),
            total_llm_call_count=sum(item.token_usage.llm_call_count for item in records),
            total_repair_rounds=sum(item.repair_rounds for item in records),
            total_first_round_errors=sum(len(item.first_round_errors) for item in records),
            total_final_differences=sum(item.final_difference_count for item in records),
        )
        validation_errors = [
            AIAnnotationMetricError(
                code=code,
                example_message=validation_examples.get(code, ""),
                count=count,
                session_count=len(validation_sessions[code]),
            )
            for code, count in validation_counts.most_common()
        ]
        final_differences = [
            AIAnnotationMetricDifference(
                category=category,
                label=_DIFFERENCE_LABELS.get(category, category),
                count=count,
            )
            for category, count in difference_counts.most_common()
        ]
        daily = [
            AIAnnotationDailyMetric(date=day, **values)
            for day, values in sorted(daily_values.items())
        ]
        return AIAnnotationMetricsResponse(
            generated_at=now,
            days=days,
            summary=summary,
            validation_errors=validation_errors,
            final_differences=final_differences,
            daily=daily,
            records=records[:limit],
        )
