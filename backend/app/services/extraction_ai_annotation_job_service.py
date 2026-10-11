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
    ExtractionAIAnnotationReview,
    ExtractionAIAnnotationSavedResult,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationConfigRequest,
    AIAnnotationGenerateRequest,
    AIAnnotationGenerationGroup,
    AIAnnotationGenerationListResponse,
    AIAnnotationGenerationSummary,
    AIAnnotationJobResponse,
    AIAnnotationPromptOverrides,
    AIAnnotationRepairRequest,
    AIAnnotationResultResponse,
    AIAnnotationTokenUsage,
)
from app.services.extraction_annotation_service import (
    ExtractionAnnotationServiceError,
    drop_meaningless_excluded_mentions,
)
from app.models.extraction_annotation import ExtractionAnnotationTask
from app.services.extraction_ai_annotation_service import (
    ExtractionAIAnnotationService,
    lineage_root,
    parse_ai_annotation_result,
    persist_ai_annotation_result,
    working_copy_for,
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

    def __init__(self, detail: str | list[dict[str, str]], status_code: int | None = None):
        super().__init__(detail)
        if status_code is not None:
            self.status_code = status_code


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
        result = parse_ai_annotation_result(json.loads(job.result_json))
        if result.document is not None:
            drop_meaningless_excluded_mentions(result.document.label)
        if result.label is not None:
            drop_meaningless_excluded_mentions(result.label)
        return result
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.exception("Unable to parse persisted AI annotation result job_id=%s", job.id)
        return None


def _quota_generation_count(rows: list[ExtractionAIAnnotationJob]) -> int:
    return sum(
        row.operation == "generate"
        and row.archived_at is None
        and row.status in {"queued", "running", "success"}
        and row.cloned_from_job_id is None
        for row in rows
    )


def _job_lineage_id(
    rows: list[ExtractionAIAnnotationJob],
    job: ExtractionAIAnnotationJob,
) -> int | None:
    by_id = {row.id: row for row in rows}
    current: ExtractionAIAnnotationJob | None = job
    seen: set[int] = set()
    while current is not None and current.operation != "generate":
        if current.id in seen or current.source_job_id is None:
            return None
        seen.add(current.id)
        current = by_id.get(current.source_job_id)
    return current.id if current is not None else None


def _json_object(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _temperature_of(job: ExtractionAIAnnotationJob) -> float | None:
    value = _json_object(job.config_json).get("temperature")
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prompt_overridden(job: ExtractionAIAnnotationJob) -> bool:
    return bool(_json_object(job.prompt_json))


def _activity_counts(rows: list[ExtractionAIAnnotationJob]) -> dict[str, int | str | None]:
    roots = [
        row for row in rows
        if row.operation == "generate" and row.archived_at is None
    ]
    active = sorted(
        (row for row in rows if row.status in _ACTIVE_STATUSES),
        key=lambda row: (_as_utc(row.created_at), row.id),
        reverse=True,
    )
    current = active[0] if active else None
    return {
        "active_generation_count": _quota_generation_count(rows),
        "generation_count": len(roots),
        "generation_limit": settings.ai_annotation_max_generations_per_task,
        "active_job_id": current.id if current is not None else None,
        "active_operation": current.operation if current is not None else None,
        "active_status": current.status if current is not None else None,
    }


def _success_source_id(
    db: Session,
    saved_row: ExtractionAIAnnotationSavedResult | None,
    root: ExtractionAIAnnotationJob | None,
) -> int | None:
    if saved_row is not None and saved_row.source_job_id is not None:
        source = db.get(ExtractionAIAnnotationJob, saved_row.source_job_id)
        if source is not None and source.status == "success":
            return source.id
    if root is not None and root.status == "success":
        return root.id
    return None


def _result_from_saved(saved: ExtractionAIAnnotationSavedResult) -> AIAnnotationResultResponse | None:
    try:
        result = parse_ai_annotation_result(json.loads(saved.result_json))
        if result.document is not None:
            drop_meaningless_excluded_mentions(result.document.label)
        if result.label is not None:
            drop_meaningless_excluded_mentions(result.label)
        return result
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
            service.capture_model_output(job_id)
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
            root = lineage_root(db, completed)
            if completed.operation == "generate":
                persist_ai_annotation_result(
                    db,
                    result,
                    requested_by=completed.requested_by,
                    source_job_id=completed.id,
                    lineage_job_id=completed.id,
                )
            elif root is None or root.status != "success":
                completed.status = "failed"
                completed.error_message = "找不到可更新的成功生成，未保存工作副本。"
            elif root.archived_at is None:
                persist_ai_annotation_result(
                    db,
                    result,
                    requested_by=completed.requested_by,
                    source_job_id=completed.id,
                    lineage_job_id=root.id,
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
    ) -> AIAnnotationJobResponse:
        annotation_service = ExtractionAIAnnotationService(self.db)
        task, passage = annotation_service._get_task_and_passage(payload.task_id)
        provider_name, model_name = annotation_service._resolve_provider_config(payload.config)
        if payload.config.provider.strip():
            payload.config.provider = provider_name
        received_at = _now()
        self._begin_immediate()

        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == task.id,
                ExtractionAIAnnotationJob.requested_by == current_user.id,
            )
            .all()
        )
        active = sorted(
            (row for row in rows if row.status in _ACTIVE_STATUSES),
            key=lambda row: (_as_utc(row.created_at), row.id),
            reverse=True,
        )
        if active:
            existing = active[0]
            self.db.commit()
            logger.info(
                "AI annotation reused active job reused_job_id=%s task_id=%s requested_by=%s",
                existing.id,
                task.id,
                current_user.id,
            )
            return self._to_response(
                existing,
                self.db.get(Passage, existing.passage_id),
                reused_existing=True,
            )

        source_job_id: int | None = None
        lineage_job_id: int | None = None
        if operation == "repair":
            try:
                source_job_id, lineage_job_id = annotation_service.require_generation_lineage(
                    task.id,
                    current_user.id,
                    payload.job_id if isinstance(payload, AIAnnotationRepairRequest) else None,
                )
            except Exception:
                self.db.rollback()
                raise
            repair_count = sum(
                row.operation == "repair" and _job_lineage_id(rows, row) == lineage_job_id
                for row in rows
            )
            repair_limit = settings.ai_annotation_max_repairs_per_generation
            if repair_count >= repair_limit:
                self.db.rollback()
                logger.warning(
                    "AI annotation repair cap task_id=%s requested_by=%s lineage_job_id=%s limit=%s",
                    task.id,
                    current_user.id,
                    lineage_job_id,
                    repair_limit,
                )
                raise ExtractionAIAnnotationJobError(
                    f"该份生成的 API 修复已达上限 {repair_limit}",
                    status_code=409,
                )
        else:
            active_count = _quota_generation_count(rows)
            limit = settings.ai_annotation_max_generations_per_task
            if active_count >= limit:
                self.db.rollback()
                logger.warning(
                    "AI annotation generation cap task_id=%s requested_by=%s lineage_job_id=%s limit=%s",
                    task.id,
                    current_user.id,
                    None,
                    limit,
                )
                raise ExtractionAIAnnotationJobError(
                    f"该篇未归档的 AI 生成已达上限 {limit}，请先归档一份再生成",
                    status_code=409,
                )

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
        if operation == "generate":
            logger.info(
                "AI annotation generate enqueued job_id=%s task_id=%s requested_by=%s provider=%s model=%s active_count=%s",
                job.id,
                task.id,
                current_user.id,
                provider_name,
                model_name,
                _quota_generation_count([*rows, job]),
            )

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

    def _begin_immediate(self) -> None:
        """结束已有的 deferred 读事务，再以 BEGIN IMMEDIATE 锁住后续写入。

        SQLAlchemy 的 SQLite 方言不接受 isolation_level="IMMEDIATE"。先切到
        AUTOCOMMIT，避免它自己先发一条 deferred BEGIN，再执行 BEGIN IMMEDIATE。
        """

        self.db.commit()
        bind = self.db.get_bind()
        if bind is None or bind.dialect.name != "sqlite":
            return
        self.db.connection(
            execution_options={"isolation_level": "AUTOCOMMIT"}
        ).exec_driver_sql("BEGIN IMMEDIATE")

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
        return self._enqueue(
            payload,
            current_user,
            operation="repair",
        )

    def list_latest_jobs(self, current_user: User) -> list[AIAnnotationJobResponse]:
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(ExtractionAIAnnotationJob.requested_by == current_user.id)
            .order_by(
                desc(ExtractionAIAnnotationJob.created_at),
                desc(ExtractionAIAnnotationJob.id),
            )
            .all()
        )
        latest_by_task: dict[int, ExtractionAIAnnotationJob] = {}
        rows_by_task: dict[int, list[ExtractionAIAnnotationJob]] = {}
        for row in rows:
            latest_by_task.setdefault(row.task_id, row)
            rows_by_task.setdefault(row.task_id, []).append(row)
        return [
            self._to_response(
                row,
                self.db.get(Passage, row.passage_id),
                legacy_fallback=True,
                task_rows=rows_by_task[row.task_id],
            )
            for row in latest_by_task.values()
        ]

    def get_job(self, job_id: int, current_user: User) -> AIAnnotationJobResponse:
        job = self.db.get(ExtractionAIAnnotationJob, job_id)
        if job is None or job.requested_by != current_user.id:
            raise ExtractionAIAnnotationJobError("未找到指定的 AI 标注任务", status_code=404)
        return self._to_response(job, self.db.get(Passage, job.passage_id))

    def list_generations(
        self,
        task_id: int,
        current_user: User,
        *,
        include_archived: bool = False,
    ) -> AIAnnotationGenerationListResponse:
        task = self.db.get(ExtractionAnnotationTask, task_id)
        if task is None:
            raise ExtractionAIAnnotationJobError("未找到指定的标注任务", status_code=404)
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == task_id,
                ExtractionAIAnnotationJob.requested_by == current_user.id,
            )
            .all()
        )
        counts = _activity_counts(rows)
        roots = [row for row in rows if row.operation == "generate"]
        visible = [
            row for row in roots
            if include_archived or row.archived_at is None
        ]
        groups: dict[tuple[str, str], list[AIAnnotationGenerationSummary]] = {}
        for root in visible:
            summary = self._generation_summary(root, rows)
            groups.setdefault((root.provider or "", root.model or ""), []).append(summary)
        ordered_groups = []
        for provider, model in sorted(groups, key=lambda item: (item[0] != "", item[0], item[1])):
            generations = groups[(provider, model)]
            generations.sort(
                key=lambda item: (
                    item.archived_at is not None,
                    -_as_utc(item.created_at).timestamp(),
                    -item.job_id,
                )
            )
            if not generations:
                continue
            ordered_groups.append(
                AIAnnotationGenerationGroup(
                    provider=provider,
                    model=model,
                    generations=generations,
                )
            )
        return AIAnnotationGenerationListResponse(
            task_id=task_id,
            groups=ordered_groups,
            **counts,
        )

    def archive_generation(
        self,
        job_id: int,
        current_user: User,
    ) -> AIAnnotationGenerationSummary:
        job = self.db.get(ExtractionAIAnnotationJob, job_id)
        if job is None or job.requested_by != current_user.id:
            raise ExtractionAIAnnotationJobError("未找到指定的 AI 标注任务", status_code=404)
        root = lineage_root(self.db, job)
        if root is None or root.requested_by != current_user.id or root.task_id != job.task_id:
            raise ExtractionAIAnnotationJobError("未找到指定的 AI 标注任务", status_code=404)
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == root.task_id,
                ExtractionAIAnnotationJob.requested_by == root.requested_by,
            )
            .all()
        )
        if any(
            row.status in _ACTIVE_STATUSES and _job_lineage_id(rows, row) == root.id
            for row in rows
        ):
            raise ExtractionAIAnnotationJobError(
                "该份生成仍有进行中的任务，完成前不能归档",
                status_code=409,
            )
        if root.archived_at is None:
            root.archived_at = _now()
            root.updated_at = _now()
            self.db.add(root)
            self.db.commit()
            self.db.refresh(root)
            logger.info(
                "AI annotation generation archived lineage_job_id=%s requested_by=%s",
                root.id,
                current_user.id,
            )
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == root.task_id,
                ExtractionAIAnnotationJob.requested_by == root.requested_by,
            )
            .all()
        )
        return self._generation_summary(root, rows)

    def _generation_summary(
        self,
        root: ExtractionAIAnnotationJob,
        rows: list[ExtractionAIAnnotationJob],
    ) -> AIAnnotationGenerationSummary:
        chain = [
            row for row in rows
            if row.id == root.id or _job_lineage_id(rows, row) == root.id
        ]
        latest = max(chain, key=lambda row: (_as_utc(row.created_at), row.id))
        saved = working_copy_for(self.db, root)
        saved_result = _result_from_saved(saved) if saved is not None else None
        validation_status = (
            saved_result.validation_status
            if saved_result is not None
            else root.first_round_validation_status
        )
        review = (
            self.db.query(ExtractionAIAnnotationReview)
            .filter(
                ExtractionAIAnnotationReview.generation_job_id == root.id,
                ExtractionAIAnnotationReview.reviewer_id == root.requested_by,
            )
            .first()
        )
        review_submitted_at = (
            review.submitted_at or review.updated_at
            if review is not None and review.status == "submitted"
            else None
        )
        return AIAnnotationGenerationSummary(
            job_id=root.id,
            task_id=root.task_id,
            passage_id=root.passage_id,
            provider=root.provider,
            model=root.model,
            temperature=_temperature_of(root),
            prompt_overridden=_prompt_overridden(root),
            status=root.status,  # type: ignore[arg-type]
            validation_status=validation_status,
            created_at=root.created_at,
            finished_at=root.finished_at,
            review_status=(review.status if review is not None else "not_started"),
            review_accuracy=(
                review.accuracy_basis_points / 10_000 if review is not None else None
            ),
            review_submitted_at=review_submitted_at,
            has_working_copy=saved is not None,
            latest_job_id=latest.id,
            latest_operation=latest.operation,  # type: ignore[arg-type]
            latest_status=latest.status,  # type: ignore[arg-type]
            latest_provider=latest.provider,
            latest_model=latest.model,
            archived_at=root.archived_at,
            repair_count=sum(row.operation == "repair" for row in chain),
        )

    def _to_response(
        self,
        job: ExtractionAIAnnotationJob,
        passage: Passage | None,
        *,
        reused_existing: bool = False,
        legacy_fallback: bool = False,
        task_rows: list[ExtractionAIAnnotationJob] | None = None,
    ) -> AIAnnotationJobResponse:
        if task_rows is None:
            task_rows = (
                self.db.query(ExtractionAIAnnotationJob)
                .filter(
                    ExtractionAIAnnotationJob.task_id == job.task_id,
                    ExtractionAIAnnotationJob.requested_by == job.requested_by,
                )
                .all()
            )
        counts = _activity_counts(task_rows)
        root = lineage_root(self.db, job)
        saved_row = working_copy_for(self.db, root)
        used_fallback = False
        if legacy_fallback and saved_row is None and job.status != "success":
            fallback = self._latest_success_root(job.task_id, job.requested_by)
            if fallback is not None:
                root = fallback
                saved_row = working_copy_for(self.db, root)
                used_fallback = True

        job_result = _result_from_job(job)
        saved_result = _result_from_saved(saved_row) if saved_row is not None else None
        source_id = _success_source_id(self.db, saved_row, root)
        if used_fallback and saved_result is None and root is not None:
            saved_result = _result_from_job(root)
            source_id = root.id
        elif (
            not used_fallback
            and legacy_fallback
            and saved_result is None
            and job.status == "success"
        ):
            saved_result = job_result
            source_id = root.id if root is not None and root.status == "success" else None
        elif not legacy_fallback and not used_fallback and saved_row is None:
            source_id = None

        review = None
        if root is not None and root.operation == "generate":
            review = (
                self.db.query(ExtractionAIAnnotationReview)
                .filter(
                    ExtractionAIAnnotationReview.generation_job_id == root.id,
                    ExtractionAIAnnotationReview.reviewer_id == job.requested_by,
                )
                .first()
            )
        review_submitted_at = (
            review.submitted_at or review.updated_at
            if review is not None and review.status == "submitted"
            else None
        )
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
            saved_result_source_job_id=source_id,
            generation_job_id=root.id if root is not None and root.operation == "generate" else None,
            lineage_job_id=root.id if root is not None and root.operation == "generate" else None,
            review_status=(review.status if review is not None else "not_started"),
            review_accuracy=(
                review.accuracy_basis_points / 10_000 if review is not None else None
            ),
            review_submitted_at=review_submitted_at,
            result=job_result,
            saved_result=saved_result,
            reused_existing=reused_existing,
            archived_at=root.archived_at if root is not None else None,
            **counts,
        )

    def _latest_success_root(
        self,
        task_id: int,
        requested_by: int,
    ) -> ExtractionAIAnnotationJob | None:
        return (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == task_id,
                ExtractionAIAnnotationJob.requested_by == requested_by,
                ExtractionAIAnnotationJob.operation == "generate",
                ExtractionAIAnnotationJob.status == "success",
                ExtractionAIAnnotationJob.archived_at.is_(None),
                ExtractionAIAnnotationJob.cloned_from_job_id.is_(None),
            )
            .order_by(
                desc(ExtractionAIAnnotationJob.created_at),
                desc(ExtractionAIAnnotationJob.id),
            )
            .first()
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
