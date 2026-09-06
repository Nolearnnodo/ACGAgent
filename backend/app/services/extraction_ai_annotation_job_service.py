"""功能 A AI 标注的持久化队列与后台执行器。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationSavedResult,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationJobResponse,
    AIAnnotationPromptOverrides,
    AIAnnotationRepairRequest,
    AIAnnotationResultResponse,
    AIAnnotationTokenUsage,
)
from app.services.extraction_annotation_service import ExtractionAnnotationServiceError
from app.services.extraction_ai_annotation_service import (
    ExtractionAIAnnotationService,
    persist_ai_annotation_result,
)

logger = logging.getLogger(__name__)
settings = get_settings()
_ai_annotation_executor = ThreadPoolExecutor(
    max_workers=max(1, settings.ai_annotation_concurrency),
    thread_name_prefix="extraction-ai-annotation",
)
_ACTIVE_STATUSES = ("queued", "running")
AIAnnotationPayload = AIAnnotationGenerateRequest | AIAnnotationRepairRequest


class ExtractionAIAnnotationJobError(ExtractionAnnotationServiceError):
    """AI 标注任务队列的可预期错误。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _client_started_at(value: datetime | None, received_at: datetime) -> datetime:
    """只接受与请求接收时刻接近的客户端点击时间，避免异常时钟污染统计。"""

    if value is None:
        return received_at
    candidate = _as_utc(value)
    if candidate < received_at - timedelta(minutes=10):
        return received_at
    if candidate > received_at + timedelta(minutes=2):
        return received_at
    return candidate


def _set_job_usage(job: ExtractionAIAnnotationJob, usage: AIAnnotationTokenUsage) -> None:
    job.prompt_tokens = usage.prompt_tokens
    job.completion_tokens = usage.completion_tokens
    job.total_tokens = usage.total_tokens
    job.prompt_cache_hit_tokens = usage.prompt_cache_hit_tokens
    job.prompt_cache_miss_tokens = usage.prompt_cache_miss_tokens
    job.cache_metrics_supported = usage.cache_metrics_supported
    job.llm_call_count = usage.llm_call_count


def _set_first_round_result(
    job: ExtractionAIAnnotationJob,
    result: AIAnnotationResultResponse,
) -> None:
    if job.operation != "generate":
        return
    job.first_round_validation_status = result.validation_status
    job.first_round_error_count = len(result.validation_issues)
    job.first_round_errors_json = json.dumps(
        [issue.model_dump(mode="json") for issue in result.validation_issues],
        ensure_ascii=False,
    )
    job.first_round_warning_count = len(result.fragment_warnings)
    job.first_round_truncated_count = len(result.truncated_fragments)


def _set_first_round_failure(job: ExtractionAIAnnotationJob, message: str) -> None:
    if job.operation != "generate":
        return
    job.first_round_validation_status = "failed"
    job.first_round_error_count = 1
    job.first_round_errors_json = json.dumps(
        [{"path": "root", "message": message, "code": "generation_failed"}],
        ensure_ascii=False,
    )


def _error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, list):
        return json.dumps(detail, ensure_ascii=False)
    if detail:
        return str(detail)
    return str(exc) or "后台标注任务失败。"


def _safe_config_json(config: AIAnnotationConfigRequest) -> str:
    data = config.model_dump(mode="json")
    # API Key 只保留在当前请求提交给后台线程的内存对象中。
    data["api_key"] = ""
    return json.dumps(data, ensure_ascii=False)


def _prompt_json(prompt_overrides: AIAnnotationPromptOverrides | None) -> str:
    return json.dumps(
        prompt_overrides.model_dump(mode="json") if prompt_overrides else {},
        ensure_ascii=False,
    )


def _payload_from_job(job: ExtractionAIAnnotationJob) -> AIAnnotationPayload:
    config_data = json.loads(job.config_json or "{}")
    if getattr(job, "operation", "generate") == "repair":
        if not job.input_content:
            raise ValueError("修复任务缺少待修复的 JSON 内容")
        return AIAnnotationRepairRequest(
            task_id=job.task_id,
            job_id=job.source_job_id,
            content=job.input_content,
            config=AIAnnotationConfigRequest.model_validate(config_data),
            client_started_at=job.client_started_at,
        )

    prompt_data = json.loads(job.prompt_json or "{}")
    return AIAnnotationGenerateRequest(
        task_id=job.task_id,
        config=AIAnnotationConfigRequest.model_validate(config_data),
        prompt_overrides=(
            AIAnnotationPromptOverrides.model_validate(prompt_data)
            if prompt_data
            else None
        ),
        client_started_at=job.client_started_at,
    )


def _result_from_job(job: ExtractionAIAnnotationJob) -> AIAnnotationResultResponse | None:
    if not job.result_json:
        return None
    try:
        return AIAnnotationResultResponse.model_validate(json.loads(job.result_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.exception("Unable to parse persisted AI annotation result job_id=%s", job.id)
        return None


def _result_from_saved(saved: ExtractionAIAnnotationSavedResult) -> AIAnnotationResultResponse | None:
    try:
        return AIAnnotationResultResponse.model_validate(json.loads(saved.result_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.exception(
            "Unable to parse persisted AI annotation saved result id=%s",
            saved.id,
        )
        return None


def _log_background_failure(future: Future[None]) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("AI annotation background job escaped worker boundary")


def _run_job_background(job_id: int, payload: AIAnnotationPayload) -> None:
    db = SessionLocal()
    service: ExtractionAIAnnotationService | None = None
    try:
        job = db.get(ExtractionAIAnnotationJob, job_id)
        if job is None or job.status not in _ACTIVE_STATUSES:
            return

        job.status = "running"
        job.started_at = _now()
        job.updated_at = _now()
        db.add(job)
        db.commit()

        try:
            service = ExtractionAIAnnotationService(db)
            result = (
                service.repair(payload)
                if isinstance(payload, AIAnnotationRepairRequest)
                else service.generate(payload)
            )
        except Exception as exc:
            db.rollback()
            failed = db.get(ExtractionAIAnnotationJob, job_id)
            if failed is not None:
                failed.status = "failed"
                failure_message = _error_message(exc)
                failed.error_message = failure_message
                failed.finished_at = _now()
                failed.updated_at = _now()
                if service is not None:
                    _set_job_usage(failed, service.token_usage)
                _set_first_round_failure(failed, failure_message)
                db.add(failed)
                db.commit()
            logger.exception("AI annotation job failed job_id=%s", job_id)
            return

        completed = db.get(ExtractionAIAnnotationJob, job_id)
        if completed is not None:
            completed.status = "success"
            completed.provider = result.provider
            completed.model = result.model
            completed.result_json = json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
            )
            completed.error_message = ""
            completed.finished_at = _now()
            completed.updated_at = _now()
            _set_job_usage(completed, result.token_usage)
            _set_first_round_result(completed, result)
            db.add(completed)
            persist_ai_annotation_result(
                db,
                result,
                requested_by=completed.requested_by,
                source_job_id=completed.id,
            )
            db.commit()
    finally:
        db.close()


def _submit_job(job_id: int, payload: AIAnnotationPayload) -> None:
    future = _ai_annotation_executor.submit(_run_job_background, job_id, payload)
    future.add_done_callback(_log_background_failure)


class ExtractionAIAnnotationJobService:
    """管理 AI 标注任务的入队、查询和启动恢复。"""

    def __init__(self, db: Session):
        self.db = db

    def _enqueue(
        self,
        payload: AIAnnotationPayload,
        current_user: User,
        *,
        operation: str,
        source_job_id: int | None = None,
    ) -> AIAnnotationJobResponse:
        annotation_service = ExtractionAIAnnotationService(self.db)
        task, passage = annotation_service._get_task_and_passage(payload.task_id)
        provider_name, model_name = annotation_service._resolve_provider_config(payload.config)
        received_at = _now()

        existing = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == task.id,
                ExtractionAIAnnotationJob.requested_by == current_user.id,
                ExtractionAIAnnotationJob.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(desc(ExtractionAIAnnotationJob.created_at))
            .first()
        )
        if existing is not None:
            return self._to_response(existing, passage)

        job = ExtractionAIAnnotationJob(
            task_id=task.id,
            passage_id=passage.doc_id,
            requested_by=current_user.id,
            operation=operation,
            source_job_id=source_job_id,
            input_content=(
                payload.content
                if isinstance(payload, AIAnnotationRepairRequest)
                else None
            ),
            status="queued",
            provider=provider_name,
            model=model_name,
            config_json=_safe_config_json(payload.config),
            prompt_json=_prompt_json(
                payload.prompt_overrides
                if isinstance(payload, AIAnnotationGenerateRequest)
                else None
            ),
            client_started_at=_client_started_at(
                getattr(payload, "client_started_at", None),
                received_at,
            ),
            created_at=received_at,
            updated_at=received_at,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        try:
            _submit_job(job.id, payload)
        except Exception as exc:
            job.status = "failed"
            failure_message = _error_message(exc)
            job.error_message = failure_message
            job.finished_at = _now()
            job.updated_at = _now()
            _set_first_round_failure(job, failure_message)
            self.db.add(job)
            self.db.commit()

        return self._to_response(job, passage)

    def enqueue(
        self,
        payload: AIAnnotationGenerateRequest,
        current_user: User,
    ) -> AIAnnotationJobResponse:
        return self._enqueue(
            payload,
            current_user,
            operation="generate",
        )

    def enqueue_repair(
        self,
        payload: AIAnnotationRepairRequest,
        current_user: User,
    ) -> AIAnnotationJobResponse:
        annotation_service = ExtractionAIAnnotationService(self.db)
        task, _passage = annotation_service._get_task_and_passage(payload.task_id)
        source_job_id = annotation_service._resolve_source_job_id(
            task.id,
            current_user.id,
            payload.job_id,
        )
        return self._enqueue(
            payload,
            current_user,
            operation="repair",
            source_job_id=source_job_id,
        )

    def list_latest_jobs(self, current_user: User) -> list[AIAnnotationJobResponse]:
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(ExtractionAIAnnotationJob.requested_by == current_user.id)
            .order_by(desc(ExtractionAIAnnotationJob.created_at))
            .all()
        )
        latest_by_task: dict[int, ExtractionAIAnnotationJob] = {}
        for row in rows:
            latest_by_task.setdefault(row.task_id, row)
        return [
            self._to_response(row, self.db.get(Passage, row.passage_id))
            for row in latest_by_task.values()
        ]

    def get_job(self, job_id: int, current_user: User) -> AIAnnotationJobResponse:
        job = self.db.get(ExtractionAIAnnotationJob, job_id)
        if job is None or job.requested_by != current_user.id:
            raise ExtractionAIAnnotationJobError("未找到指定的 AI 标注任务", status_code=404)
        return self._to_response(job, self.db.get(Passage, job.passage_id))

    def _to_response(
        self,
        job: ExtractionAIAnnotationJob,
        passage: Passage | None,
    ) -> AIAnnotationJobResponse:
        job_result = _result_from_job(job)
        saved_row = (
            self.db.query(ExtractionAIAnnotationSavedResult)
            .filter(
                ExtractionAIAnnotationSavedResult.task_id == job.task_id,
                ExtractionAIAnnotationSavedResult.requested_by == job.requested_by,
            )
            .first()
        )
        saved_result = _result_from_saved(saved_row) if saved_row is not None else None
        # 兼容迁移前已完成的历史任务：历史 job.result_json 仍作为当前结果返回，
        # 下次生成或重新校验时会补写新表。
        if saved_result is None and job.status == "success":
            saved_result = job_result
        return AIAnnotationJobResponse(
            id=job.id,
            task_id=job.task_id,
            passage_id=job.passage_id,
            passage_title=passage.title if passage is not None else f"任务 #{job.task_id}",
            operation=getattr(job, "operation", "generate"),
            status=job.status,  # type: ignore[arg-type]
            provider=job.provider,
            model=job.model,
            error_message=job.error_message,
            client_started_at=job.client_started_at,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            submitted_at=job.submitted_at,
            token_usage=AIAnnotationTokenUsage(
                prompt_tokens=job.prompt_tokens,
                completion_tokens=job.completion_tokens,
                total_tokens=job.total_tokens,
                prompt_cache_hit_tokens=job.prompt_cache_hit_tokens,
                prompt_cache_miss_tokens=job.prompt_cache_miss_tokens,
                cache_metrics_supported=job.cache_metrics_supported,
                llm_call_count=job.llm_call_count,
            ),
            first_round_validation_status=job.first_round_validation_status,
            first_round_error_count=job.first_round_error_count,
            first_round_warning_count=job.first_round_warning_count,
            first_round_truncated_count=job.first_round_truncated_count,
            final_difference_count=job.final_difference_count,
            saved_result_source_job_id=(
                saved_row.source_job_id if saved_row is not None else None
            ),
            result=job_result,
            saved_result=saved_result,
        )


def recover_pending_ai_annotation_jobs() -> None:
    """应用重启时恢复排队任务，并将中断中的任务明确标记为失败。"""

    pending: list[tuple[int, AIAnnotationPayload]] = []
    db = SessionLocal()
    try:
        interrupted = (
            db.query(ExtractionAIAnnotationJob)
            .filter(ExtractionAIAnnotationJob.status == "running")
            .all()
        )
        for job in interrupted:
            job.status = "failed"
            job.error_message = "服务重启时任务被中断，请重新发送标注请求。"
            job.finished_at = _now()
            job.updated_at = _now()
            _set_first_round_failure(job, job.error_message)
            db.add(job)

        queued = (
            db.query(ExtractionAIAnnotationJob)
            .filter(ExtractionAIAnnotationJob.status == "queued")
            .order_by(ExtractionAIAnnotationJob.created_at)
            .all()
        )
        for job in queued:
            try:
                pending.append((job.id, _payload_from_job(job)))
            except Exception as exc:
                job.status = "failed"
                job.error_message = f"任务恢复失败：{_error_message(exc)}"
                job.finished_at = _now()
                job.updated_at = _now()
                _set_first_round_failure(job, job.error_message)
                db.add(job)
        db.commit()
    finally:
        db.close()

    for job_id, payload in pending:
        try:
            _submit_job(job_id, payload)
        except Exception:
            logger.exception("Unable to requeue AI annotation job_id=%s", job_id)
