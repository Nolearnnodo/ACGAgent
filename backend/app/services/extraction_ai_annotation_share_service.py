"""把 canonical admin 的 AI 标注结果同步到其他账号。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
    ExtractionAIAnnotationSavedResult,
)
from app.models.user import User
from app.schemas.extraction_annotation import AIAnnotationResultResponse
from app.services.extraction_annotation_service import drop_meaningless_excluded_mentions
from app.services.extraction_ai_annotation_service import persist_ai_annotation_result

logger = logging.getLogger(__name__)

CANONICAL_AI_ANNOTATION_EMAIL = "admin@example.com"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_dict(row: object) -> dict[str, Any]:
    table = getattr(row, "__table__", None)
    if table is None:
        return {}
    payload: dict[str, Any] = {}
    for column in table.columns:
        payload[column.key] = getattr(row, column.key)
    return payload


class ExtractionAIAnnotationShareService:
    """备份各账号私有结果，并把 admin 的成功首轮结果复制到其他用户。"""

    def __init__(self, db: Session):
        self.db = db

    def backup_private_annotations(self) -> dict[str, Any]:
        users = self.db.query(User).order_by(User.id.asc()).all()
        payload_users: list[dict[str, Any]] = []
        for user in users:
            payload_users.append(
                {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role,
                    "created_at": user.created_at,
                    "jobs": [
                        _row_dict(job)
                        for job in (
                            self.db.query(ExtractionAIAnnotationJob)
                            .filter(ExtractionAIAnnotationJob.requested_by == user.id)
                            .order_by(ExtractionAIAnnotationJob.id.asc())
                            .all()
                        )
                    ],
                    "saved_results": [
                        _row_dict(saved)
                        for saved in (
                            self.db.query(ExtractionAIAnnotationSavedResult)
                            .filter(ExtractionAIAnnotationSavedResult.requested_by == user.id)
                            .order_by(ExtractionAIAnnotationSavedResult.id.asc())
                            .all()
                        )
                    ],
                    "reviews": [
                        _row_dict(review)
                        for review in (
                            self.db.query(ExtractionAIAnnotationReview)
                            .filter(ExtractionAIAnnotationReview.reviewer_id == user.id)
                            .order_by(ExtractionAIAnnotationReview.id.asc())
                            .all()
                        )
                    ],
                }
            )
        return {
            "created_at": _now().isoformat(),
            "users": payload_users,
        }

    def canonical_admin(self) -> User | None:
        admin = (
            self.db.query(User)
            .filter(User.email == CANONICAL_AI_ANNOTATION_EMAIL)
            .first()
        )
        if admin is not None:
            return admin
        return (
            self.db.query(User)
            .filter(User.role == "admin")
            .order_by(User.id.asc())
            .first()
        )

    def sync_all_users(self) -> dict[str, int]:
        admin = self.canonical_admin()
        if admin is None:
            return {"copied_users": 0, "copied_jobs": 0, "updated_jobs": 0}
        copied_users = 0
        copied_jobs = 0
        updated_jobs = 0
        targets = (
            self.db.query(User)
            .filter(User.id != admin.id)
            .order_by(User.id.asc())
            .all()
        )
        for user in targets:
            summary = self.sync_user(user, admin=admin)
            copied_jobs += summary["copied_jobs"]
            updated_jobs += summary["updated_jobs"]
            if summary["copied_jobs"] or summary["updated_jobs"]:
                copied_users += 1
        return {
            "copied_users": copied_users,
            "copied_jobs": copied_jobs,
            "updated_jobs": updated_jobs,
        }

    def sync_user(
        self,
        user: User,
        *,
        admin: User | None = None,
    ) -> dict[str, int]:
        source_admin = admin or self.canonical_admin()
        if source_admin is None or user.id == source_admin.id:
            return {"copied_jobs": 0, "updated_jobs": 0}

        copied_jobs = 0
        updated_jobs = 0
        archived_clones = 0
        for source in self._canonical_jobs(source_admin.id):
            existing = (
                self.db.query(ExtractionAIAnnotationJob)
                .filter(
                    ExtractionAIAnnotationJob.requested_by == user.id,
                    ExtractionAIAnnotationJob.cloned_from_job_id == source.id,
                )
                .first()
            )
            if existing is None:
                existing = self._clone_job(source, user)
                if source.archived_at is not None:
                    existing.archived_at = source.archived_at
                    archived_clones += 1
                copied_jobs += 1
            else:
                self._copy_job_payload(source, existing)
                if source.archived_at is not None and existing.archived_at is None:
                    existing.archived_at = source.archived_at
                    archived_clones += 1
                updated_jobs += 1
            self.db.add(existing)
            self.db.flush()
            result = self._result_for(source)
            if result is None:
                continue
            persist_ai_annotation_result(
                self.db,
                result,
                requested_by=user.id,
                source_job_id=existing.id,
                lineage_job_id=existing.id,
            )
        logger.info(
            "AI annotation sync user_id=%s copied_jobs=%s updated_jobs=%s archived_clones=%s",
            user.id,
            copied_jobs,
            updated_jobs,
            archived_clones,
        )
        return {
            "copied_jobs": copied_jobs,
            "updated_jobs": updated_jobs,
            "archived_clones": archived_clones,
        }

    def _canonical_jobs(self, admin_id: int) -> list[ExtractionAIAnnotationJob]:
        rows = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.requested_by == admin_id,
                ExtractionAIAnnotationJob.operation == "generate",
                ExtractionAIAnnotationJob.status == "success",
                ExtractionAIAnnotationJob.cloned_from_job_id.is_(None),
                ExtractionAIAnnotationJob.result_json.is_not(None),
            )
            .order_by(
                desc(ExtractionAIAnnotationJob.created_at),
                desc(ExtractionAIAnnotationJob.id),
            )
            .all()
        )
        return rows

    def _clone_job(
        self,
        source: ExtractionAIAnnotationJob,
        user: User,
    ) -> ExtractionAIAnnotationJob:
        clone = ExtractionAIAnnotationJob(
            task_id=source.task_id,
            passage_id=source.passage_id,
            requested_by=user.id,
            cloned_from_job_id=source.id,
        )
        self._copy_job_payload(source, clone)
        clone.created_at = _now()
        clone.updated_at = clone.created_at
        return clone

    def _copy_job_payload(
        self,
        source: ExtractionAIAnnotationJob,
        target: ExtractionAIAnnotationJob,
    ) -> None:
        target.operation = "generate"
        target.source_job_id = None
        target.input_content = None
        target.status = source.status
        target.provider = source.provider
        target.model = source.model
        target.config_json = source.config_json
        target.prompt_json = source.prompt_json
        target.result_json = source.result_json
        target.error_message = source.error_message
        target.client_started_at = source.client_started_at
        target.started_at = source.started_at
        target.finished_at = source.finished_at
        target.submitted_at = None
        target.prompt_tokens = source.prompt_tokens
        target.completion_tokens = source.completion_tokens
        target.total_tokens = source.total_tokens
        target.prompt_cache_hit_tokens = source.prompt_cache_hit_tokens
        target.prompt_cache_miss_tokens = source.prompt_cache_miss_tokens
        target.cache_metrics_supported = source.cache_metrics_supported
        target.llm_call_count = source.llm_call_count
        target.first_round_validation_status = source.first_round_validation_status
        target.first_round_error_count = source.first_round_error_count
        target.first_round_errors_json = source.first_round_errors_json
        target.first_round_warning_count = source.first_round_warning_count
        target.first_round_truncated_count = source.first_round_truncated_count
        target.final_difference_count = source.final_difference_count
        target.final_differences_json = source.final_differences_json
        target.updated_at = _now()

    def _result_for(
        self,
        source: ExtractionAIAnnotationJob,
    ) -> AIAnnotationResultResponse | None:
        saved = (
            self.db.query(ExtractionAIAnnotationSavedResult)
            .filter(
                ExtractionAIAnnotationSavedResult.task_id == source.task_id,
                ExtractionAIAnnotationSavedResult.requested_by == source.requested_by,
                ExtractionAIAnnotationSavedResult.lineage_job_id == source.id,
            )
            .one_or_none()
        )
        raw = saved.result_json if saved is not None else source.result_json
        if not raw:
            return None
        try:
            result = AIAnnotationResultResponse.model_validate(json.loads(raw))
            if result.document is not None:
                drop_meaningless_excluded_mentions(result.document.label)
            if result.label is not None:
                drop_meaningless_excluded_mentions(result.label)
            return result
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.exception(
                "Unable to parse canonical AI annotation result job_id=%s",
                source.id,
            )
            return None
