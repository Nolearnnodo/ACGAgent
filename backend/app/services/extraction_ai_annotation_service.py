"""功能 A 的 AI 标注调用、结果解析与格式校验。"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.providers.deepseek_provider import DeepSeekProvider
from app.models.extraction_annotation import (
    ExtractionAnnotationSubmission,
    ExtractionAnnotationTask,
    ExtractionGoldVersion,
)
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationSavedResult,
)
from app.models.passage import Passage
from app.schemas.extraction_annotation import (
    EVENT_TYPES,
    AIAnnotationConfigRequest,
    AIAnnotationExportDocument,
    AIAnnotationExportMetadata,
    AIAnnotationExportPassage,
    AIAnnotationGenerateRequest,
    AIAnnotationPromptRequest,
    AIAnnotationPromptOverrides,
    AIAnnotationRepairRequest,
    AIAnnotationPromptResponse,
    AIAnnotationResultResponse,
    AIAnnotationTaskContextResponse,
    AIAnnotationTokenUsage,
    AIAnnotationValidationIssue,
    AnnotationPassageResponse,
    ExtractionAnnotationLabel,
    EvidenceSpan,
    PersonAnnotation,
    PassageAnnotation,
)
from app.services.extraction_annotation_service import (
    ExtractionAnnotationServiceError,
    codepoint_to_utf16_offset,
    context_sha256,
    drop_meaningless_excluded_mentions,
    validate_for_submission,
)
from app.services.extraction_ai_annotation_prompt import (
    AI_ANNOTATION_AUXILIARY_FRAGMENT_SYSTEM_PROMPT,
    AI_ANNOTATION_EVENT_FRAGMENT_SYSTEM_PROMPT,
    AI_ANNOTATION_HISTORICAL_FRAGMENT_SYSTEM_PROMPT,
    AI_ANNOTATION_PROMPT_VERSION,
    AI_ANNOTATION_PASSAGE_PERSON_SYSTEM_PROMPT,
    AI_ANNOTATION_RELATION_FRAGMENT_SYSTEM_PROMPT,
    AI_ANNOTATION_SYSTEM_PROMPT,
)


logger = logging.getLogger(__name__)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_SCHEMA_FIT_ATTEMPTS = 5

_FRAGMENT_RETRY_LIMIT = 2
_RELATION_SOURCE_BATCH_SIZE = 4


@dataclass
class _RemoteAnnotationCall:
    content: str
    model: str
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    cache_metrics_supported: bool = False


@dataclass
class _AnnotationTokenAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    cache_metrics_supported: bool = False
    llm_call_count: int = 0

    def add(self, call: _RemoteAnnotationCall) -> None:
        self.prompt_tokens += max(call.prompt_tokens, 0)
        self.completion_tokens += max(call.completion_tokens, 0)
        self.total_tokens += max(
            call.total_tokens or call.prompt_tokens + call.completion_tokens,
            0,
        )
        self.prompt_cache_hit_tokens += max(call.prompt_cache_hit_tokens, 0)
        self.prompt_cache_miss_tokens += max(call.prompt_cache_miss_tokens, 0)
        self.cache_metrics_supported = (
            self.cache_metrics_supported or call.cache_metrics_supported
        )
        self.llm_call_count += 1

    def to_schema(self) -> AIAnnotationTokenUsage:
        return AIAnnotationTokenUsage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            prompt_cache_hit_tokens=self.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.prompt_cache_miss_tokens,
            cache_metrics_supported=self.cache_metrics_supported,
            llm_call_count=self.llm_call_count,
        )


class AIAnnotationServiceError(ExtractionAnnotationServiceError):
    """AI 标注接口的可预期错误。"""

    def __init__(self, detail: str | list[dict[str, str]], status_code: int | None = None):
        super().__init__(detail)
        if status_code is not None:
            self.status_code = status_code


class AIAnnotationConfigError(AIAnnotationServiceError):
    status_code = 400


class AIAnnotationProviderError(AIAnnotationServiceError):
    status_code = 502


def lineage_root(
    db: Session,
    job: ExtractionAIAnnotationJob | None,
) -> ExtractionAIAnnotationJob | None:
    """沿 repair 的 source_job_id 走到 generate 根。断链返回 None。"""

    current = job
    seen: set[int] = set()
    while current is not None and current.operation != "generate":
        if current.id in seen or current.source_job_id is None:
            return None
        seen.add(current.id)
        current = db.get(ExtractionAIAnnotationJob, current.source_job_id)
    return current


def working_copy_for(
    db: Session,
    root: ExtractionAIAnnotationJob | None,
) -> ExtractionAIAnnotationSavedResult | None:
    if root is None:
        return None
    return (
        db.query(ExtractionAIAnnotationSavedResult)
        .filter(
            ExtractionAIAnnotationSavedResult.task_id == root.task_id,
            ExtractionAIAnnotationSavedResult.requested_by == root.requested_by,
            ExtractionAIAnnotationSavedResult.lineage_job_id == root.id,
        )
        .one_or_none()
    )


def persist_ai_annotation_result(
    db: Session,
    result: AIAnnotationResultResponse,
    *,
    requested_by: int,
    source_job_id: int | None = None,
    lineage_job_id: int | None = None,
) -> None:
    """按生成根保存工作副本。lineage 为空时只更新已有的 NULL 槽，不新插。"""

    query = db.query(ExtractionAIAnnotationSavedResult).filter(
        ExtractionAIAnnotationSavedResult.task_id == result.task_id,
        ExtractionAIAnnotationSavedResult.requested_by == requested_by,
    )
    if lineage_job_id is None:
        saved = query.filter(
            ExtractionAIAnnotationSavedResult.lineage_job_id.is_(None)
        ).one_or_none()
        if saved is None:
            return
    else:
        saved = query.filter(
            ExtractionAIAnnotationSavedResult.lineage_job_id == lineage_job_id
        ).one_or_none()
        if saved is None:
            saved = ExtractionAIAnnotationSavedResult(
                task_id=result.task_id,
                passage_id=result.passage_id,
                requested_by=requested_by,
                lineage_job_id=lineage_job_id,
            )
    saved.passage_id = result.passage_id
    saved.source_job_id = source_job_id
    saved.lineage_job_id = lineage_job_id
    saved.result_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    saved.updated_at = datetime.now(timezone.utc)
    db.add(saved)


def _issue(path: str, message: str, code: str) -> AIAnnotationValidationIssue:
    return AIAnnotationValidationIssue(path=path, message=message, code=code)


def _parse_json_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("模型没有返回内容")

    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        # 兼容少数模型在 JSON 前后附带一句说明的情况，但最终仍只接受 JSON 对象。
        first = text.find("{")
        last = text.rfind("}")
        if first < 0 or last <= first:
            raise ValueError("模型输出不是合法 JSON 对象") from None
        try:
            document = json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            raise ValueError("模型输出不是合法 JSON 对象") from None

    if not isinstance(document, dict):
        raise ValueError("标注结果根节点必须是 JSON 对象")
    return document


def _validation_issues_from_pydantic(exc: ValidationError) -> list[AIAnnotationValidationIssue]:
    return [
        _issue(
            ".".join(str(part) for part in error.get("loc", ())) or "root",
            str(error.get("msg", "字段校验失败")),
            "invalid_label_schema",
        )
        for error in exc.errors()
    ]


def _issue_path(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "root"


def _unwrap_annotation(annotation: Any) -> Any:
    if get_origin(annotation) is Annotated:
        return _unwrap_annotation(get_args(annotation)[0])
    return annotation


def _model_from_annotation(annotation: Any) -> type[Any] | None:
    annotation = _unwrap_annotation(annotation)
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            found = _model_from_annotation(arg)
            if found is not None:
                return found
        return None
    if isinstance(annotation, type):
        try:
            if issubclass(annotation, BaseModel):
                return annotation
        except TypeError:
            return None
    return None


def _list_item_annotation(annotation: Any) -> Any:
    annotation = _unwrap_annotation(annotation)
    if get_origin(annotation) is list:
        args = get_args(annotation)
        return args[0] if args else Any
    return Any


def _annotation_at(model: type[Any], loc: tuple[Any, ...]) -> Any:
    current_model = model
    annotation: Any = None
    for index, part in enumerate(loc):
        if isinstance(part, int):
            annotation = _list_item_annotation(annotation)
            item_model = _model_from_annotation(annotation)
            if item_model is not None:
                current_model = item_model
            continue
        field = current_model.model_fields[str(part)]
        annotation = field.annotation
        nested = _model_from_annotation(annotation)
        if nested is not None and index + 1 < len(loc) and isinstance(loc[index + 1], str):
            current_model = nested
    return annotation


def _dig(payload: Any, loc: tuple[Any, ...]) -> Any:
    current = payload
    for part in loc:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return None
    return current


def _put(payload: Any, loc: tuple[Any, ...], value: Any) -> bool:
    parent = payload if len(loc) == 1 else _dig(payload, loc[:-1])
    key = loc[-1]
    if isinstance(parent, dict) and isinstance(key, str) and key in parent:
        parent[key] = value
        return True
    if isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        parent[key] = value
        return True
    return False


def _assign_model(root: Any, loc: tuple[Any, ...], value: Any) -> None:
    current = root
    for part in loc[:-1]:
        current = current[part] if isinstance(part, int) else getattr(current, str(part))
    key = loc[-1]
    if isinstance(key, int):
        current[key] = value
    else:
        setattr(current, str(key), value)


def _schema_mismatch_issue(error: dict[str, Any], original: Any) -> AIAnnotationValidationIssue:
    loc = tuple(error.get("loc") or ())
    max_length = (error.get("ctx") or {}).get("max_length")
    if error.get("type") == "string_too_long" and isinstance(original, str) and isinstance(max_length, int):
        message = f"与 schema 不符：最多 {max_length} 个字符，当前 {len(original)} 个，原文已保留。"
    elif error.get("type") == "too_long" and isinstance(original, list) and isinstance(max_length, int):
        message = f"与 schema 不符：最多 {max_length} 条，当前 {len(original)} 条，原列表已保留。"
    else:
        message = f"与 schema 不符：{error.get('msg', '字段校验失败')}。原文已保留。"
    return _issue(_issue_path(loc), message, "schema_mismatch")


def _dedupe_issues(
    issues: list[AIAnnotationValidationIssue],
) -> list[AIAnnotationValidationIssue]:
    unique: list[AIAnnotationValidationIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.path, issue.code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def load_model_keeping_original(
    model: type[Any],
    payload: Any,
) -> tuple[Any, list[AIAnnotationValidationIssue]]:
    """校验模型，但字符串和列表超出上限时保留原文，只记录 schema 不符。"""

    try:
        return model.model_validate(payload), []
    except ValidationError as first_exc:
        if not any(error.get("type") in {"string_too_long", "too_long"} for error in first_exc.errors()):
            raise

    current = copy.deepcopy(payload)
    issues: list[AIAnnotationValidationIssue] = []
    string_restorations: list[tuple[tuple[Any, ...], str]] = []
    list_restorations: list[tuple[tuple[Any, ...], list[Any]]] = []
    seen: set[str] = set()
    last_exc: ValidationError | None = None
    for _ in range(_SCHEMA_FIT_ATTEMPTS):
        try:
            loaded = model.model_validate(current)
        except ValidationError as exc:
            last_exc = exc
            progressed = False
            for error in exc.errors():
                err_type = error.get("type")
                if err_type not in {"string_too_long", "too_long"}:
                    continue
                loc = tuple(error.get("loc") or ())
                max_length = (error.get("ctx") or {}).get("max_length")
                if not loc or not isinstance(max_length, int):
                    continue
                original = _dig(payload, loc)
                path = _issue_path(loc)
                if err_type == "string_too_long" and isinstance(original, str) and len(original) > max_length:
                    existing = _dig(current, loc)
                    if isinstance(existing, str) and len(existing) <= max_length:
                        continue
                    if not _put(current, loc, original[:max_length]):
                        continue
                    progressed = True
                    if path not in seen:
                        seen.add(path)
                        issues.append(_schema_mismatch_issue(error, original))
                        string_restorations.append((loc, original))
                elif err_type == "too_long" and isinstance(original, list) and len(original) > max_length:
                    existing = _dig(current, loc)
                    if isinstance(existing, list) and len(existing) <= max_length:
                        continue
                    if not _put(current, loc, original[:max_length]):
                        continue
                    progressed = True
                    if path not in seen:
                        seen.add(path)
                        issues.append(_schema_mismatch_issue(error, original))
                        list_restorations.append((loc, original))
            if not progressed:
                raise
            continue

        for loc, original in string_restorations:
            _assign_model(loaded, loc, original)
        for loc, original in list_restorations:
            item_annotation = _list_item_annotation(_annotation_at(model, loc))
            item_model = _model_from_annotation(item_annotation)
            if item_model is None:
                _assign_model(loaded, loc, original)
                continue
            coerced: list[Any] = []
            prefix = _issue_path(loc)
            for index, item in enumerate(original):
                if not isinstance(item, dict):
                    coerced.append(item)
                    continue
                nested, nested_issues = load_model_keeping_original(item_model, item)
                for nested_issue in nested_issues:
                    nested_path = nested_issue.path if nested_issue.path != "root" else ""
                    nested_issue.path = f"{prefix}.{index}.{nested_path}" if nested_path else f"{prefix}.{index}"
                issues.extend(nested_issues)
                coerced.append(nested)
            _assign_model(loaded, loc, coerced)
        return loaded, _dedupe_issues(issues)

    if last_exc is not None:
        raise last_exc
    raise ValidationError.from_exception_data(model.__name__, [])


def parse_ai_annotation_result(data: dict[str, Any]) -> AIAnnotationResultResponse:
    """读回已保存的 AI 结果。超出 schema 长度的原文仍保留在对象里。"""

    result, _issues = load_model_keeping_original(AIAnnotationResultResponse, data)
    return result


class ExtractionAIAnnotationService:
    """对指定标注任务执行分片 AI 抽取，并在服务端合并结果。"""

    def __init__(self, db: Session):
        self.db = db
        self._token_usage = _AnnotationTokenAccumulator()
        self._model_output_job_id: int | None = None

    def capture_model_output(self, job_id: int) -> None:
        """把后续模型原文写到这条任务上，供失败后人工补回。"""

        self._model_output_job_id = job_id

    def _remember_model_output(self, stage: str, content: Any) -> None:
        """先提交模型原文。后面的校验失败回滚不会把它清掉，接口也不返回它。"""

        job_id = self._model_output_job_id
        if job_id is None:
            return
        try:
            job = self.db.get(ExtractionAIAnnotationJob, job_id)
            if job is None:
                return
            try:
                stored = json.loads(job.model_output_json or "{}")
            except json.JSONDecodeError:
                stored = {}
            if not isinstance(stored, dict):
                stored = {}
            fragments = stored.get("fragments")
            if not isinstance(fragments, list):
                fragments = []
            fragments.append({"stage": stage, "content": content})
            stored["fragments"] = fragments
            job.model_output_json = json.dumps(stored, ensure_ascii=False, default=str)
            self.db.add(job)
            previous = self.db.expire_on_commit
            self.db.expire_on_commit = False
            try:
                self.db.commit()
            finally:
                self.db.expire_on_commit = previous
        except Exception:
            logger.exception("Unable to store AI annotation model output job_id=%s", job_id)

    @property
    def token_usage(self) -> AIAnnotationTokenUsage:
        """返回当前生成/修复过程已经实际发生的模型调用用量。"""

        return self._token_usage.to_schema()

    def generate(
        self,
        payload: AIAnnotationGenerateRequest,
    ) -> AIAnnotationResultResponse:
        task, passage = self._get_task_and_passage(payload.task_id)
        config = payload.config
        provider_name, model_name = self._resolve_provider_config(config)
        started = time.perf_counter()
        self._token_usage = _AnnotationTokenAccumulator()
        # 模型调用可能持续数分钟。先结束这次读取，避免占住 SQLite，
        # 否则切换篇目、入队和保存都会一直等待。
        self._release_db_for_model_call()

        if provider_name == "mock":
            # Mock 是离线格式演示，不具备第二个真实模型调用；线上 Provider 会执行格式复核轮次。
            raw_content = self._mock_content(task, passage)
            resolved_model = model_name or "mock-ai-annotation"
            return self._validate_content(
                task,
                passage,
                raw_content,
                source="generated",
                provider=provider_name,
                model=resolved_model,
                elapsed_ms=max(int((time.perf_counter() - started) * 1000), 0),
                token_usage=self.token_usage,
            )

        provider = self._build_provider(config, model_name)
        (
            assembled_document,
            resolved_model,
            fragment_count,
            truncated_fragments,
            fragment_warnings,
        ) = (
            self._generate_staged_document(
                task,
                passage,
                config,
                provider,
                prompt_overrides=payload.prompt_overrides,
            )
        )
        raw_content = json.dumps(assembled_document, ensure_ascii=False, indent=2)
        return self._validate_content(
            task,
            passage,
            raw_content,
            source="generated",
            provider=provider_name,
            model=resolved_model,
            elapsed_ms=max(int((time.perf_counter() - started) * 1000), 0),
            fragment_count=fragment_count,
            truncated_fragments=truncated_fragments,
            fragment_warnings=fragment_warnings,
            token_usage=self.token_usage,
        )

    def repair(
        self,
        payload: AIAnnotationRepairRequest,
        *,
        requested_by: int | None = None,
    ) -> AIAnnotationResultResponse:
        """按分片重新抽取当前文献，避免把完整 JSON 再交给模型重生成。"""

        task, passage = self._get_task_and_passage(payload.task_id)
        source_job_id: int | None = None
        lineage_job_id: int | None = None
        if requested_by is not None:
            source_job_id, lineage_job_id = self.require_generation_lineage(
                task.id,
                requested_by,
                payload.job_id,
            )
        provider_name, model_name = self._resolve_provider_config(payload.config)
        self._token_usage = _AnnotationTokenAccumulator()
        if provider_name == "mock":
            raise AIAnnotationConfigError(
                "Mock 模式不支持继续调用格式复核模型，请切换到远程 Provider。"
            )

        started = time.perf_counter()
        provider = self._build_provider(payload.config, model_name)

        # 先用服务端规则定位当前结果的错误类型。只有根 JSON 或 label
        # 无法解析时，才回退到完整的语义分片重建；普通规则错误只修复
        # 对应的人物、事件、关系或辅助数组分片。
        current_result = self._validate_content(
            task,
            passage,
            payload.content,
            source="validated",
            provider=provider_name,
            model=model_name,
            elapsed_ms=0,
        )
        self._release_db_for_model_call()
        if current_result.label is None:
            (
                assembled_document,
                resolved_model,
                fragment_count,
                truncated_fragments,
                fragment_warnings,
            ) = self._generate_staged_document(
                task,
                passage,
                payload.config,
                provider,
            )
        else:
            (
                repaired_label,
                resolved_model,
                fragment_count,
                truncated_fragments,
                fragment_warnings,
            ) = self._repair_label_by_issues(
                task,
                passage,
                current_result.label,
                current_result.validation_issues,
                payload.config,
                provider,
            )
            assembled_document = self._build_export_document(task, passage, repaired_label)
        repaired_content = json.dumps(assembled_document, ensure_ascii=False, indent=2)
        result = self._validate_content(
            task,
            passage,
            repaired_content,
            source="generated",
            provider=provider_name,
            model=resolved_model or model_name,
            elapsed_ms=max(int((time.perf_counter() - started) * 1000), 0),
            fragment_count=fragment_count,
            truncated_fragments=truncated_fragments,
            fragment_warnings=fragment_warnings,
            token_usage=self.token_usage,
        )
        if requested_by is not None and lineage_job_id is not None:
            persist_ai_annotation_result(
                self.db,
                result,
                requested_by=requested_by,
                source_job_id=source_job_id,
                lineage_job_id=lineage_job_id,
            )
            self.db.commit()
        return result

    def validate(
        self,
        task_id: int,
        content: str,
        *,
        requested_by: int | None = None,
        job_id: int | None = None,
    ) -> AIAnnotationResultResponse:
        task, passage = self._get_task_and_passage(task_id)
        started = time.perf_counter()
        result = self._validate_content(
            task,
            passage,
            content,
            source="validated",
            provider="",
            model="",
            elapsed_ms=max(int((time.perf_counter() - started) * 1000), 0),
        )
        if requested_by is not None:
            source_job_id, lineage_job_id = self.require_generation_lineage(
                task_id,
                requested_by,
                job_id,
            )
            persist_ai_annotation_result(
                self.db,
                result,
                requested_by=requested_by,
                source_job_id=source_job_id,
                lineage_job_id=lineage_job_id,
            )
            self.db.commit()
        return result

    def require_generation_lineage(
        self,
        task_id: int,
        requested_by: int,
        job_id: int | None,
    ) -> tuple[int, int]:
        """校验或修复只能写到未归档的成功 generate 根。返回 (source_job_id, lineage_job_id)。"""

        if job_id is None:
            root = (
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
                    ExtractionAIAnnotationJob.created_at.desc(),
                    ExtractionAIAnnotationJob.id.desc(),
                )
                .first()
            )
            if root is None:
                raise AIAnnotationServiceError("未找到可保存的成功生成", status_code=404)
            return root.id, root.id

        job = self.db.get(ExtractionAIAnnotationJob, job_id)
        if job is None or job.requested_by != requested_by or job.task_id != task_id:
            raise AIAnnotationServiceError("未找到当前用户可保存的 AI 标注任务", status_code=404)
        root = lineage_root(self.db, job)
        if (
            root is None
            or root.operation != "generate"
            or root.status != "success"
            or root.requested_by != requested_by
            or root.task_id != task_id
            or root.archived_at is not None
        ):
            raise AIAnnotationServiceError(
                "只有未归档的成功生成才能校验或修复",
                status_code=409,
            )
        return job.id, root.id

    def _resolve_source_job_id(
        self,
        task_id: int,
        requested_by: int,
        job_id: int | None,
    ) -> int | None:
        if job_id is not None:
            job = self.db.get(ExtractionAIAnnotationJob, job_id)
            if job is None or job.requested_by != requested_by or job.task_id != task_id:
                raise AIAnnotationServiceError("未找到当前用户可保存的 AI 标注任务", status_code=404)
            return job.id

        latest_job = (
            self.db.query(ExtractionAIAnnotationJob)
            .filter(
                ExtractionAIAnnotationJob.task_id == task_id,
                ExtractionAIAnnotationJob.requested_by == requested_by,
                ExtractionAIAnnotationJob.status == "success",
            )
            .order_by(ExtractionAIAnnotationJob.created_at.desc())
            .first()
        )
        return latest_job.id if latest_job is not None else None

    def _release_db_for_model_call(self) -> None:
        """结束当前事务，但保留已加载的篇目对象，供随后的模型调用使用。"""

        previous = self.db.expire_on_commit
        self.db.expire_on_commit = False
        try:
            self.db.commit()
        finally:
            self.db.expire_on_commit = previous

    def get_task_context(self, task_id: int) -> AIAnnotationTaskContextResponse:
        task, passage = self._get_task_and_passage(task_id)
        return AIAnnotationTaskContextResponse(
            task_id=task.id,
            context_sha256=task.context_sha256,
            spec_version=task.spec_version,
            status=task.status,
            passage=AnnotationPassageResponse(
                doc_id=passage.doc_id,
                title=passage.title,
                context=passage.context,
                source_type=passage.source_type,
                workflow_status=passage.workflow_status,
            ),
        )

    def preview_prompt(
        self,
        payload: AIAnnotationPromptRequest,
    ) -> AIAnnotationPromptResponse:
        task, passage = self._get_task_and_passage(payload.task_id)
        messages = self._build_messages(task, passage, payload.config)
        return AIAnnotationPromptResponse(
            task_id=task.id,
            prompt_version=AI_ANNOTATION_PROMPT_VERSION,
            system_prompt=messages[0]["content"],
            user_prompt=messages[1]["content"],
        )

    @staticmethod
    def _build_provider(
        config: AIAnnotationConfigRequest,
        model_name: str,
    ) -> DeepSeekProvider:
        settings = get_settings()
        return DeepSeekProvider(
            api_key=config.api_key.strip() or settings.llm_api_key,
            api_base_url=config.base_url.strip() or settings.llm_api_base_url,
            model_name=model_name,
            temperature=config.temperature,
            include_temperature=config.temperature is not None,
            disable_timeout=False,
        )

    def _call_remote_model(
        self,
        provider: DeepSeekProvider,
        messages: list[dict[str, str]],
        *,
        stage: str,
        purpose: str,
    ) -> _RemoteAnnotationCall:
        try:
            call_result = provider.chat_completion_with_usage(
                messages,
                {
                    "response_format": "json",
                    "purpose": purpose,
                    # 标注一次请求包含长正文和严格 JSON 输出，失败后由持久化
                    # 任务记录失败并允许用户重试，避免后台无提示地重复昂贵调用。
                    "retry": False,
                },
            )
        except ValueError as exc:
            raise AIAnnotationConfigError(
                f"{stage}模型调用配置不完整，请检查 API Key、Base URL 和模型名称。"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise AIAnnotationProviderError(
                f"{stage}模型服务返回 HTTP {exc.response.status_code}，请检查接口地址和调用额度。"
            ) from exc
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise AIAnnotationProviderError(
                f"{stage}模型服务请求失败，请检查网络和服务状态。"
            ) from exc
        except RuntimeError as exc:
            detail = " ".join(str(exc).split())
            if "authentication failed" in detail.lower():
                message = f"{stage}模型鉴权或调用失败，请检查调用配置。"
            else:
                message = f"{stage}模型调用失败：{detail[:180] or '请检查调用配置。'}"
            raise AIAnnotationProviderError(message) from exc
        except Exception as exc:
            raise AIAnnotationProviderError(f"{stage}模型调用失败，请检查调用配置和服务日志。") from exc

        content = call_result.content or ""
        raw_response = getattr(call_result, "raw_response", {}) or {}
        choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
        first_choice = choices[0] if isinstance(choices, list) and choices else {}
        finish_reason = (
            str(first_choice.get("finish_reason") or "")
            if isinstance(first_choice, dict)
            else ""
        )
        usage = getattr(call_result, "usage", None)
        call = _RemoteAnnotationCall(
            content=content,
            model=getattr(call_result, "model", "") or "",
            finish_reason=finish_reason,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            prompt_cache_hit_tokens=int(
                getattr(usage, "prompt_cache_hit_tokens", 0) or 0
            ),
            prompt_cache_miss_tokens=int(
                getattr(usage, "prompt_cache_miss_tokens", 0) or 0
            ),
            cache_metrics_supported=bool(
                getattr(usage, "cache_metrics_supported", False)
            ),
        )
        self._token_usage.add(call)
        return call

    def _repair_label_by_issues(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        label: ExtractionAnnotationLabel,
        issues: list[AIAnnotationValidationIssue],
        config: AIAnnotationConfigRequest,
        provider: DeepSeekProvider,
    ) -> tuple[ExtractionAnnotationLabel, str, int, list[str], list[str]]:
        """按校验路径修复标签，避免把无关内容再次交给模型生成。"""

        payload = label.model_dump(mode="python")
        issue_payload = [issue.model_dump(mode="json") for issue in issues]
        persons = payload.setdefault("persons", [])
        payload.setdefault("person_relations", [])
        payload.setdefault("excluded_mentions", [])
        payload.setdefault("unresolved_items", [])
        payload.setdefault("schema_conflicts", [])

        passage_repair_needed = False
        person_repair_needed = False
        auxiliary_fields: set[str] = set()
        event_targets: set[tuple[int, str]] = set()
        all_event_targets: set[int] = set()
        historical_targets: set[int] = set()
        relation_issue_indices: set[int] = set()
        relation_repair_needed = False
        relation_repair_all = False
        clear_level_three: set[int] = set()

        wrapper_only_codes = {
            "invalid_passage",
            "passage_id_mismatch",
            "context_mismatch",
            "context_digest_mismatch",
        }

        for issue in issues:
            path = issue.path or ""
            code = issue.code or ""
            parts = path.split(".")

            if code in {
                "evidence_out_of_range",
                "evidence_quote_mismatch",
                "evidence_utf16_mismatch",
            }:
                # quote 仍能在当前正文中定位时，后面的 normalize 会直接
                # 重算 code point/UTF-16 坐标；只有 quote 本身不存在时才
                # 需要把该证据所属的语义分片交给模型重做。
                quote = self._evidence_quote_for_issue_path(payload, parts)
                if code == "evidence_utf16_mismatch" or (
                    quote and quote in passage.context
                ):
                    continue

            if parts[0] == "passage":
                if code not in wrapper_only_codes:
                    passage_repair_needed = True
                continue
            if parts[0] in {"metadata", "schema_version"}:
                # wrapper 和 schema_version 都由服务端重新组装，无需调用模型。
                continue
            if parts[0] in {"excluded_mentions", "unresolved_items", "schema_conflicts"}:
                auxiliary_fields.add(parts[0])
                continue
            if parts[0] == "person_relations":
                relation_repair_needed = True
                if len(parts) >= 2 and parts[1].isdigit():
                    relation_issue_indices.add(int(parts[1]))
                else:
                    relation_repair_all = True
                continue
            if parts[0] != "persons":
                continue

            if len(parts) < 2 or not parts[1].isdigit():
                person_repair_needed = True
                continue
            person_index = int(parts[1])
            if code == "level_three_has_events":
                clear_level_three.add(person_index)
                continue

            if len(parts) >= 3 and parts[2] == "historical_events":
                historical_targets.add(person_index)
                continue
            if len(parts) >= 3 and parts[2] in {"event_checks", "life_events"}:
                event_type = self._event_type_for_issue_path(payload, person_index, parts)
                if event_type is None:
                    all_event_targets.add(person_index)
                else:
                    event_targets.add((person_index, event_type))
                continue
            person_repair_needed = True

        # 三级人物不能拥有任何主评测事件，这是确定性的 schema 修复，不需要再问模型。
        for person_index in clear_level_three:
            if 0 <= person_index < len(persons):
                persons[person_index]["life_events"] = []
                persons[person_index]["historical_events"] = []
                persons[person_index]["event_checks"] = {
                    event_type: "unreviewed" for event_type in EVENT_TYPES
                }
        event_targets = {
            (person_index, event_type)
            for person_index, event_type in event_targets
            if person_index not in clear_level_three
        }
        for person_index in all_event_targets:
            if person_index not in clear_level_three:
                event_targets.update(
                    (person_index, event_type) for event_type in EVENT_TYPES
                )

        calls = 0
        models: list[str] = []
        truncated_fragments: list[str] = []
        fragment_warnings: list[str] = []

        def record_fragment(result: tuple[dict[str, Any], int, list[str], list[str], list[str]]) -> dict[str, Any]:
            nonlocal calls
            calls += result[1]
            models.extend(result[2])
            truncated_fragments.extend(result[3])
            fragment_warnings.extend(result[4])
            return result[0]

        def append_person_warning(person: dict[str, Any], stage: str, warning: str) -> None:
            person.setdefault("_unresolved_items", []).append(
                {
                    "category": "repair_fragment_failed",
                    "note": f"{stage} 修复分片未返回可解析内容，待人工复核：{warning}",
                }
            )

        def append_label_warning(stage: str, warning: str) -> None:
            payload.setdefault("unresolved_items", []).append(
                {
                    "category": "repair_fragment_failed",
                    "note": f"{stage} 修复分片未返回可解析内容，待人工复核：{warning}",
                }
            )

        person_issue_payload = [
            item
            for item in issue_payload
            if item["path"] == "passage"
            or item["path"].startswith("passage.")
            or item["path"] == "persons"
            or (
                item["path"].startswith("persons.")
                and ".event_checks." not in item["path"]
                and ".life_events." not in item["path"]
                and ".historical_events." not in item["path"]
                and item["code"] != "level_three_has_events"
            )
        ]

        if passage_repair_needed or person_repair_needed:
            passage_person_fragment = record_fragment(
                self._call_fragment_json(
                    provider,
                    self._build_fragment_messages(
                        task,
                        passage,
                        system_prompt=AI_ANNOTATION_PASSAGE_PERSON_SYSTEM_PROMPT,
                        payload={
                            "stage": "repair_passage_person",
                            "current_annotation": {
                                "passage": payload.get("passage") or {},
                                "persons": [
                                    self._person_summary_for_model(person)
                                    for person in persons
                                    if isinstance(person, dict)
                                ],
                            },
                            "repair_issues": person_issue_payload,
                            "extra_instruction": config.extra_instruction.strip(),
                        },
                        prompt_overrides=None,
                    ),
                    stage="repair_passage_person",
                    purpose="extraction_ai_annotation_passage_person_repair",
                )
            )
            if passage_person_fragment.get("_fragment_warning"):
                append_label_warning(
                    "passage_person",
                    str(passage_person_fragment["_fragment_warning"]),
                )
            else:
                repaired_passage = self._assemble_passage_person_fragment(
                    task,
                    passage,
                    passage_person_fragment,
                )
                if passage_repair_needed and repaired_passage.get("passage"):
                    payload["passage"] = repaired_passage["passage"]
                if person_repair_needed:
                    old_persons = [dict(person) for person in persons]
                    new_persons = repaired_passage.get("persons") or []
                    if new_persons:
                        if len(new_persons) != len(old_persons) and payload["person_relations"]:
                            relation_repair_needed = True
                            relation_repair_all = True
                        key_map = {
                            str(old.get("key")): str(new.get("key"))
                            for old, new in zip(old_persons, new_persons)
                            if old.get("key") and new.get("key")
                        }
                        for index, new_person in enumerate(new_persons):
                            if index >= len(old_persons):
                                continue
                            old_person = old_persons[index]
                            new_person["event_checks"] = dict(
                                old_person.get("event_checks")
                                or {event_type: "unreviewed" for event_type in EVENT_TYPES}
                            )
                            new_person["life_events"] = list(old_person.get("life_events") or [])
                            new_person["historical_events"] = list(
                                old_person.get("historical_events") or []
                            )
                        for relation in payload["person_relations"]:
                            if not isinstance(relation, dict):
                                continue
                            for endpoint in ("source_person_key", "target_person_key"):
                                old_key = str(relation.get(endpoint) or "")
                                if old_key in key_map:
                                    relation[endpoint] = key_map[old_key]
                        payload["persons"] = new_persons
                        persons = new_persons
                    else:
                        append_label_warning("passage_person", "empty_persons")

        event_issue_for = lambda person_index, event_type: [
            item
            for item in issue_payload
            if item["path"].startswith(f"persons.{person_index}.")
            and (
                f"event_checks.{event_type}" in item["path"]
                or ".life_events." in item["path"]
            )
        ]
        for person_index, event_type in sorted(event_targets):
            if not (0 <= person_index < len(persons)):
                continue
            person = persons[person_index]
            if person.get("level") == 3:
                continue
            current_events = [
                event
                for event in person.get("life_events") or []
                if isinstance(event, dict) and event.get("event_type") == event_type
            ]
            event_fragment = record_fragment(
                self._call_fragment_json(
                    provider,
                    self._build_fragment_messages(
                        task,
                        passage,
                        system_prompt=AI_ANNOTATION_EVENT_FRAGMENT_SYSTEM_PROMPT,
                        payload={
                            "stage": "repair_life_event",
                            "person": self._person_summary_for_model(person),
                            "person_key": person.get("key", f"p{person_index + 1}"),
                            "event_type": event_type,
                            "current_events": current_events,
                            "repair_issues": event_issue_for(person_index, event_type),
                        },
                        prompt_overrides=None,
                    ),
                    stage=f"repair_life_event_{person.get('key', person_index)}_{event_type}",
                    purpose="extraction_ai_annotation_life_event_repair",
                )
            )
            if event_fragment.get("_fragment_warning"):
                append_person_warning(
                    person,
                    f"life_event_{event_type}",
                    str(event_fragment["_fragment_warning"]),
                )
                continue
            person["life_events"] = [
                event
                for event in person.get("life_events") or []
                if not (isinstance(event, dict) and event.get("event_type") == event_type)
            ]
            self._merge_event_fragment(person, event_fragment, event_type)

        historical_issue_for = lambda person_index: [
            item
            for item in issue_payload
            if item["path"].startswith(f"persons.{person_index}.historical_events")
        ]
        for person_index in sorted(historical_targets):
            if not (0 <= person_index < len(persons)):
                continue
            person = persons[person_index]
            if person.get("level") == 3:
                continue
            historical_fragment = record_fragment(
                self._call_fragment_json(
                    provider,
                    self._build_fragment_messages(
                        task,
                        passage,
                        system_prompt=AI_ANNOTATION_HISTORICAL_FRAGMENT_SYSTEM_PROMPT,
                        payload={
                            "stage": "repair_historical_event",
                            "person": self._person_summary_for_model(person),
                            "person_key": person.get("key", f"p{person_index + 1}"),
                            "current_events": list(person.get("historical_events") or []),
                            "repair_issues": historical_issue_for(person_index),
                        },
                        prompt_overrides=None,
                    ),
                    stage=f"repair_historical_event_{person.get('key', person_index)}",
                    purpose="extraction_ai_annotation_historical_event_repair",
                )
            )
            if historical_fragment.get("_fragment_warning"):
                append_person_warning(
                    person,
                    "historical_event",
                    str(historical_fragment["_fragment_warning"]),
                )
                continue
            person["historical_events"] = list(
                historical_fragment.get("historical_events") or []
            )
            person.setdefault("_unresolved_items", []).extend(
                historical_fragment.get("unresolved_items") or []
            )
            person.setdefault("_schema_conflicts", []).extend(
                historical_fragment.get("schema_conflicts") or []
            )

        base_relations = list(payload.get("person_relations") or [])
        relation_source_keys: set[str] = set()
        person_order = {
            str(person.get("key")): index
            for index, person in enumerate(persons)
            if person.get("key")
        }
        for relation_index in relation_issue_indices:
            if 0 <= relation_index < len(base_relations):
                relation = base_relations[relation_index]
                if isinstance(relation, dict) and relation.get("source_person_key"):
                    source_key = str(relation["source_person_key"])
                    target_key = str(relation.get("target_person_key") or "")
                    if source_key in person_order and target_key in person_order:
                        # 分片统一按人物顺序选择两端中较早者作为 source。
                        canonical_source = min(
                            (source_key, target_key), key=person_order.__getitem__
                        )
                        relation_source_keys.add(canonical_source)
                    else:
                        relation_repair_all = True
                else:
                    relation_repair_all = True
            else:
                relation_repair_all = True

        if person_repair_needed and base_relations:
            # 人物顺序/数量改变时，关系端点也必须重新裁定。
            relation_repair_needed = True
            if len(persons) != len(label.persons):
                relation_repair_all = True

        if relation_repair_needed:
            valid_relation_sources = [
                str(person.get("key")) for person in persons if person.get("key")
            ]
            if relation_repair_all or not relation_source_keys:
                relation_source_keys = set(valid_relation_sources)
            source_order = [
                source for source in valid_relation_sources if source in relation_source_keys
            ]
            relation_replacements: list[dict[str, Any]] = []
            replaced_sources: set[str] = set()
            replaced_relation_indices: set[int] = set()
            relation_issue_payload = [
                item for item in issue_payload if item["path"].startswith("person_relations")
            ]
            for start in range(0, len(source_order), _RELATION_SOURCE_BATCH_SIZE):
                source_keys = source_order[start : start + _RELATION_SOURCE_BATCH_SIZE]
                relation_fragment = record_fragment(
                    self._call_fragment_json(
                        provider,
                        self._build_fragment_messages(
                            task,
                            passage,
                            system_prompt=AI_ANNOTATION_RELATION_FRAGMENT_SYSTEM_PROMPT,
                            payload={
                                "stage": "repair_person_relation",
                                "source_person_keys": source_keys,
                                "persons": [
                                    self._person_summary_for_model(person)
                                    for person in persons
                                ],
                                "current_relations": [
                                    self._relation_summary_for_model(relation)
                                    for relation in base_relations
                                    if isinstance(relation, dict)
                                    and (
                                        relation.get("source_person_key") in source_keys
                                        or relation_repair_all
                                    )
                                ],
                                "repair_issues": relation_issue_payload,
                            },
                            prompt_overrides=None,
                        ),
                        stage=f"repair_person_relation_{start // _RELATION_SOURCE_BATCH_SIZE + 1}",
                        purpose="extraction_ai_annotation_relation_repair",
                    )
                )
                if relation_fragment.get("_fragment_warning"):
                    append_label_warning(
                        f"person_relation_{start // _RELATION_SOURCE_BATCH_SIZE + 1}",
                        str(relation_fragment["_fragment_warning"]),
                    )
                    continue
                replaced_sources.update(source_keys)
                replaced_relation_indices.update(relation_issue_indices)
                relation_replacements.extend(
                    relation
                    for relation in relation_fragment.get("person_relations") or []
                    if isinstance(relation, dict)
                )
                payload.setdefault("unresolved_items", []).extend(
                    relation_fragment.get("unresolved_items") or []
                )
                payload.setdefault("schema_conflicts", []).extend(
                    relation_fragment.get("schema_conflicts") or []
                )
            payload["person_relations"] = [
                relation
                for relation_index, relation in enumerate(base_relations)
                if relation_index not in replaced_relation_indices
                and not (
                    isinstance(relation, dict)
                    and relation.get("source_person_key") in replaced_sources
                )
            ] + relation_replacements

        if auxiliary_fields:
            auxiliary_fragment = record_fragment(
                self._call_fragment_json(
                    provider,
                    self._build_fragment_messages(
                        task,
                        passage,
                        system_prompt=AI_ANNOTATION_AUXILIARY_FRAGMENT_SYSTEM_PROMPT,
                        payload={
                            "stage": "repair_auxiliary_arrays",
                            "current_annotation": {
                                field: payload.get(field) or [] for field in auxiliary_fields
                            },
                            "repair_issues": [
                                item
                                for item in issue_payload
                                if item["path"].split(".", 1)[0] in auxiliary_fields
                            ],
                        },
                        prompt_overrides=None,
                    ),
                    stage="repair_auxiliary_arrays",
                    purpose="extraction_ai_annotation_auxiliary_repair",
                )
            )
            if auxiliary_fragment.get("_fragment_warning"):
                append_label_warning(
                    "auxiliary_arrays",
                    str(auxiliary_fragment["_fragment_warning"]),
                )
            else:
                for field in auxiliary_fields:
                    if field in auxiliary_fragment:
                        payload[field] = list(auxiliary_fragment.get(field) or [])

        repaired_label = self._normalize_assembled_label(task, passage, payload)
        return (
            repaired_label,
            models[-1] if models else "",
            calls,
            truncated_fragments,
            fragment_warnings,
        )

    @staticmethod
    def _event_type_for_issue_path(
        payload: dict[str, Any],
        person_index: int,
        parts: list[str],
    ) -> str | None:
        if len(parts) >= 4 and parts[2] == "event_checks" and parts[3] in EVENT_TYPES:
            return parts[3]
        if len(parts) >= 4 and parts[2] == "life_events" and parts[3].isdigit():
            persons = payload.get("persons") or []
            if 0 <= person_index < len(persons):
                events = persons[person_index].get("life_events") or []
                event_index = int(parts[3])
                if 0 <= event_index < len(events):
                    event_type = events[event_index].get("event_type")
                    if event_type in EVENT_TYPES:
                        return event_type
        return None

    @staticmethod
    def _evidence_quote_for_issue_path(
        payload: dict[str, Any],
        parts: list[str],
    ) -> str:
        try:
            if parts[0] == "persons" and parts[1].isdigit():
                person = (payload.get("persons") or [])[int(parts[1])]
                if parts[2] == "mentions" and parts[3].isdigit():
                    evidence = (person.get("mentions") or [])[int(parts[3])]
                elif (
                    parts[2] in {"life_events", "historical_events"}
                    and parts[3].isdigit()
                    and parts[4] == "evidence"
                    and parts[5].isdigit()
                ):
                    event = (person.get(parts[2]) or [])[int(parts[3])]
                    evidence = (event.get("evidence") or [])[int(parts[5])]
                else:
                    return ""
            elif parts[0] == "person_relations" and parts[1].isdigit():
                relation = (payload.get("person_relations") or [])[int(parts[1])]
                evidence = (relation.get("evidence") or [])[int(parts[3])]
            elif parts[0] in {
                "excluded_mentions",
                "unresolved_items",
                "schema_conflicts",
            } and parts[1].isdigit():
                item = (payload.get(parts[0]) or [])[int(parts[1])]
                evidence = (item.get("evidence") or [])[int(parts[3])]
            else:
                return ""
            return str(evidence.get("quote") or "") if isinstance(evidence, dict) else ""
        except (IndexError, KeyError, TypeError, ValueError):
            return ""

    @staticmethod
    def _relation_summary_for_model(relation: dict[str, Any]) -> dict[str, Any]:
        result = dict(relation)
        result["evidence"] = ExtractionAIAnnotationService._compact_evidence_list(
            relation.get("evidence")
        )
        return result

    def _generate_staged_document(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        config: AIAnnotationConfigRequest,
        provider: DeepSeekProvider,
        *,
        prompt_overrides: AIAnnotationPromptOverrides | None = None,
    ) -> tuple[dict[str, Any], str, int, list[str], list[str]]:
        """按语义分片调用模型，并在服务端组装唯一的完整导出文档。

        模型永远不会收到“请重新生成整个当前 JSON”的复核任务。每个失败或疑似
        截断的分片只会在自己的输入范围内重试；最终冗余字段和偏移量由服务端生成。
        """

        calls = 0
        models: list[str] = []
        truncated_fragments: list[str] = []
        fragment_warnings: list[str] = []

        passage_fragment = self._call_fragment_json(
            provider,
            self._build_fragment_messages(
                task,
                passage,
                system_prompt=AI_ANNOTATION_PASSAGE_PERSON_SYSTEM_PROMPT,
                payload={
                    "stage": "passage_person",
                    "extra_instruction": config.extra_instruction.strip(),
                },
                prompt_overrides=prompt_overrides,
            ),
            stage="材料和人物分片",
            purpose="extraction_ai_annotation_passage_person",
        )
        calls += passage_fragment[1]
        models.extend(passage_fragment[2])
        truncated_fragments.extend(passage_fragment[3])
        fragment_warnings.extend(passage_fragment[4])
        passage_data = passage_fragment[0]

        label_payload = self._assemble_passage_person_fragment(
            task,
            passage,
            passage_data,
        )
        persons = label_payload["persons"]

        # 三级人物只保留人物信息；一、二级人物分别执行生平和历史事件分片，
        # 这样一个人物的事件数量也不会把整篇输出推到单次上限。
        for person in persons:
            if person.get("level") == 3:
                person["event_checks"] = {
                    event_type: "unreviewed" for event_type in EVENT_TYPES
                }
                continue

            for event_type in EVENT_TYPES:
                event_fragment = self._call_fragment_json(
                    provider,
                    self._build_fragment_messages(
                        task,
                        passage,
                        system_prompt=AI_ANNOTATION_EVENT_FRAGMENT_SYSTEM_PROMPT,
                        payload={
                            "stage": "life_event",
                            "person": self._person_summary_for_model(person),
                            "person_key": person["key"],
                            "event_type": event_type,
                        },
                        prompt_overrides=prompt_overrides,
                    ),
                    stage=f"人物 {person['key']} 的 {event_type} 事件分片",
                    purpose="extraction_ai_annotation_life_event_fragment",
                )
                calls += event_fragment[1]
                models.extend(event_fragment[2])
                truncated_fragments.extend(event_fragment[3])
                fragment_warnings.extend(event_fragment[4])
                self._merge_event_fragment(person, event_fragment[0], event_type)

            historical_fragment = self._call_fragment_json(
                provider,
                self._build_fragment_messages(
                    task,
                    passage,
                    system_prompt=AI_ANNOTATION_HISTORICAL_FRAGMENT_SYSTEM_PROMPT,
                    payload={
                        "stage": "historical_event",
                        "person": self._person_summary_for_model(person),
                        "person_key": person["key"],
                    },
                    prompt_overrides=prompt_overrides,
                ),
                stage=f"人物 {person['key']} 的历史事件分片",
                purpose="extraction_ai_annotation_historical_event_fragment",
            )
            calls += historical_fragment[1]
            models.extend(historical_fragment[2])
            truncated_fragments.extend(historical_fragment[3])
            fragment_warnings.extend(historical_fragment[4])
            self._merge_event_fragment(person, historical_fragment[0], "historical")

        # 关系按 source 人物分组；每个请求最多四个 source，遇到截断时由
        # _call_fragment_json 仅重试当前关系批次，不会重新生成前面的内容。
        relation_sources = [person["key"] for person in persons if person.get("key")]
        for start in range(0, len(relation_sources), _RELATION_SOURCE_BATCH_SIZE):
            source_keys = relation_sources[start : start + _RELATION_SOURCE_BATCH_SIZE]
            relation_fragment = self._call_fragment_json(
                provider,
                self._build_fragment_messages(
                    task,
                    passage,
                    system_prompt=AI_ANNOTATION_RELATION_FRAGMENT_SYSTEM_PROMPT,
                    payload={
                        "stage": "person_relation",
                        "source_person_keys": source_keys,
                        "persons": [self._person_summary_for_model(person) for person in persons],
                    },
                    prompt_overrides=prompt_overrides,
                ),
                stage=f"人物关系分片 {start // _RELATION_SOURCE_BATCH_SIZE + 1}",
                purpose="extraction_ai_annotation_relation_fragment",
            )
            calls += relation_fragment[1]
            models.extend(relation_fragment[2])
            truncated_fragments.extend(relation_fragment[3])
            fragment_warnings.extend(relation_fragment[4])
            label_payload["person_relations"].extend(
                relation_fragment[0].get("person_relations") or []
            )
            self._register_fragment_warning(
                label_payload,
                relation_fragment[0],
                stage=f"人物关系分片 {start // _RELATION_SOURCE_BATCH_SIZE + 1}",
            )
            for field in ("unresolved_items", "schema_conflicts", "excluded_mentions"):
                label_payload[field].extend(relation_fragment[0].get(field) or [])

        label = self._normalize_assembled_label(task, passage, label_payload)
        document = self._build_export_document(task, passage, label)
        return (
            document,
            (models[-1] if models else ""),
            calls,
            truncated_fragments,
            fragment_warnings,
        )

    @staticmethod
    def _build_export_document(
        task: ExtractionAnnotationTask,
        passage: Passage,
        label: ExtractionAnnotationLabel,
    ) -> dict[str, Any]:
        return {
            "metadata": {
                "task_id": task.id,
                "spec_version": task.spec_version,
                "context_sha256": task.context_sha256,
            },
            "passage": {
                "doc_id": passage.doc_id,
                "title": passage.title,
                "context": passage.context,
                "context_sha256": task.context_sha256,
            },
            "label": label.model_dump(mode="json"),
        }

    def _build_fragment_messages(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        prompt_overrides: AIAnnotationPromptOverrides | None,
    ) -> list[dict[str, str]]:
        user_payload = {
            "task_id": task.id,
            "spec_version": task.spec_version,
            "title": passage.title,
            "context_sha256": task.context_sha256,
            "context": passage.context,
            **payload,
        }
        system = system_prompt
        if prompt_overrides is not None and prompt_overrides.system_prompt.strip():
            # 提示词面板的自定义内容仍作为附加约束生效，但不能覆盖分片契约。
            custom_system = prompt_overrides.system_prompt.strip()
            if len(custom_system) > 8_000:
                custom_system = custom_system[:8_000]
            system += "\n\n以下是本次用户自定义的附加约束；若与本分片 JSON 契约冲突，以本分片契约为准：\n" + custom_system
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _call_fragment_json(
        self,
        provider: DeepSeekProvider,
        messages: list[dict[str, str]],
        *,
        stage: str,
        purpose: str,
    ) -> tuple[dict[str, Any], int, list[str], list[str], list[str]]:
        models: list[str] = []
        truncated: list[str] = []
        warnings: list[str] = []
        last_error = ""
        for attempt in range(_FRAGMENT_RETRY_LIMIT + 1):
            call = self._call_remote_model(
                provider,
                messages,
                stage=stage,
                purpose=purpose,
            )
            self._remember_model_output(stage, call.content)
            models.append(call.model)
            if call.finish_reason == "length":
                truncated.append(stage)
                last_error = "finish_reason=length"
            elif not call.content.strip():
                last_error = "empty_content"
            else:
                try:
                    fragment = _parse_json_content(call.content)
                    return fragment, attempt + 1, models, truncated, warnings
                except ValueError as exc:
                    last_error = str(exc)

            if attempt < _FRAGMENT_RETRY_LIMIT:
                retry_payload = {
                    "retry_instruction": (
                        "上一次分片输出未完整结束或不是可解析 JSON。请只返回本分片允许的字段，"
                        "减少 note/evidence 数量，确保在输出上限前闭合 JSON；不得输出解释。"
                    ),
                }
                retry_messages = list(messages)
                retry_user = json.loads(retry_messages[-1]["content"])
                retry_user.update(retry_payload)
                retry_messages[-1] = {
                    "role": "user",
                    "content": json.dumps(retry_user, ensure_ascii=False),
                }
                messages = retry_messages

        if purpose != "extraction_ai_annotation_passage_person":
            warning = f"{stage}: {last_error or 'fragment_failed'}"
            warnings.append(warning)
            return (
                {"_fragment_warning": warning},
                _FRAGMENT_RETRY_LIMIT + 1,
                models,
                truncated,
                warnings,
            )
        raise AIAnnotationProviderError(
            f"{stage}输出未完成（{last_error}），已只重试当前分片 { _FRAGMENT_RETRY_LIMIT } 次；"
            "请降低单片复杂度或稍后重试。"
        )

    @staticmethod
    def _person_summary_for_model(person: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": person.get("key", ""),
            "name_surface": person.get("name_surface", ""),
            "completed_name": person.get("completed_name", ""),
            "courtesy_name": person.get("courtesy_name", ""),
            "hao": person.get("hao", ""),
            "titles": person.get("titles", []),
            "level": person.get("level", 3),
            "level_reason": person.get("level_reason", ""),
            "mentions": person.get("mentions", []),
        }

    def _assemble_passage_person_fragment(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        fragment: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(fragment.get("label"), dict):
            # 兼容用户在提示词面板中仍要求完整 label 的自定义提示；后续
            # 分片仍由服务端接管，不能让这一兼容分支恢复整篇复核流程。
            fragment = fragment["label"]
        passage_data = fragment.get("passage")
        if not isinstance(passage_data, dict):
            passage_data = {}
        persons: list[dict[str, Any]] = []
        for index, raw_person in enumerate(fragment.get("persons") or [], start=1):
            if not isinstance(raw_person, dict):
                continue
            person = dict(raw_person)
            # 人物键由服务端按分片返回顺序生成，避免模型在不同重试中
            # 改写 key 导致事件/关系无法合并。
            key = f"p{index}"
            person["key"] = key
            person["mentions"] = self._compact_evidence_list(person.get("mentions"))
            person.setdefault("event_checks", {event_type: "unreviewed" for event_type in EVENT_TYPES})
            persons.append(person)

        return {
            "schema_version": task.spec_version,
            "passage": passage_data,
            "persons": persons,
            "person_relations": [],
            "excluded_mentions": list(fragment.get("excluded_mentions") or []),
            "unresolved_items": list(fragment.get("unresolved_items") or []),
            "schema_conflicts": list(fragment.get("schema_conflicts") or []),
        }

    @staticmethod
    def _compact_evidence_list(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in raw[:50]:
            if not isinstance(item, dict) or not str(item.get("quote") or "").strip():
                continue
            compact = {"quote": str(item["quote"])}
            if isinstance(item.get("start"), int) and isinstance(item.get("end"), int):
                compact.update(start=item["start"], end=item["end"])
            result.append(compact)
        return result

    def _merge_event_fragment(
        self,
        person: dict[str, Any],
        fragment: dict[str, Any],
        event_kind: str,
    ) -> None:
        fragment_warning = fragment.get("_fragment_warning")
        if event_kind == "historical":
            person["historical_events"] = list(fragment.get("historical_events") or [])
        else:
            person.setdefault("event_checks", {})
            checks = fragment.get("event_checks")
            if isinstance(checks, dict):
                check_value = checks.get(event_kind)
            else:
                check_value = fragment.get("event_check")
            if check_value is not None:
                person["event_checks"][event_kind] = self._normalize_check_state(check_value)
            elif fragment_warning:
                person["event_checks"][event_kind] = "uncertain"
            person.setdefault("life_events", []).extend(
                event
                for event in (fragment.get("life_events") or [])
                if isinstance(event, dict) and event.get("event_type", event_kind) == event_kind
            )

        person.setdefault("life_events", [])
        person.setdefault("historical_events", [])
        person.setdefault("_unresolved_items", []).extend(fragment.get("unresolved_items") or [])
        person.setdefault("_schema_conflicts", []).extend(fragment.get("schema_conflicts") or [])
        if fragment_warning:
            person["_unresolved_items"].append(
                {
                    "category": "fragment_empty_response",
                    "note": f"{event_kind}分片没有返回可解析内容，待人工复核。",
                }
            )

    @staticmethod
    def _register_fragment_warning(
        label_payload: dict[str, Any],
        fragment: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        warning = fragment.get("_fragment_warning")
        if not warning:
            return
        label_payload.setdefault("unresolved_items", []).append(
            {
                "category": "fragment_empty_response",
                "note": f"{stage}没有返回可解析内容，待人工复核。",
            }
        )

    def _normalize_assembled_label(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        payload: dict[str, Any],
    ) -> ExtractionAnnotationLabel:
        persons = payload.get("persons") or []
        for person in persons:
            person["mentions"] = self._materialize_person_mentions(
                passage.context,
                person.get("mentions"),
                prefix=f"{person.get('key', 'p')}-m",
            )
            person["life_events"] = [
                self._normalize_life_event(passage.context, event, f"{person.get('key', 'p')}-e{index}")
                for index, event in enumerate(person.get("life_events") or [], start=1)
                if isinstance(event, dict)
            ]
            person["historical_events"] = [
                self._normalize_historical_event(passage.context, event, f"{person.get('key', 'p')}-h{index}")
                for index, event in enumerate(person.get("historical_events") or [], start=1)
                if isinstance(event, dict)
            ]
        payload["unresolved_items"] = list(payload.get("unresolved_items") or [])
        payload["schema_conflicts"] = list(payload.get("schema_conflicts") or [])
        payload["excluded_mentions"] = list(payload.get("excluded_mentions") or [])
        for person in persons:
            # 事件分片的待处理项需要上收至标签级数组。
            payload["unresolved_items"].extend(person.pop("_unresolved_items", []) if isinstance(person, dict) else [])
            payload["schema_conflicts"].extend(person.pop("_schema_conflicts", []) if isinstance(person, dict) else [])

        relations: list[dict[str, Any]] = []
        for index, relation in enumerate(payload.get("person_relations") or [], start=1):
            if not isinstance(relation, dict):
                continue
            item = dict(relation)
            item["key"] = f"r{index}"
            item["evidence"] = self._materialize_evidence(
                passage.context,
                item.get("evidence"),
                prefix=f"{item['key']}-x",
            )
            relations.append(item)
        payload["person_relations"] = relations

        for field, prefix in (
            ("excluded_mentions", "x"),
            ("unresolved_items", "u"),
            ("schema_conflicts", "c"),
        ):
            normalized: list[dict[str, Any]] = []
            for index, item in enumerate(payload[field], start=1):
                if not isinstance(item, dict):
                    continue
                value = dict(item)
                value["key"] = str(value.get("key") or f"{prefix}{index}")
                value["evidence"] = self._materialize_evidence(
                    passage.context,
                    value.get("evidence"),
                    prefix=f"{value['key']}-x",
                )
                normalized.append(value)
            payload[field] = normalized

        payload["schema_version"] = task.spec_version
        payload["passage"] = payload.get("passage") or {}
        self._remember_model_output("assembled", payload)
        label, _schema_issues = load_model_keeping_original(ExtractionAnnotationLabel, payload)
        drop_meaningless_excluded_mentions(label)
        return label

    def _normalize_life_event(
        self,
        context: str,
        raw: dict[str, Any],
        key: str,
    ) -> dict[str, Any]:
        item = dict(raw)
        item["key"] = key
        time_payload = dict(item.get("time") or {})
        time_payload["state"] = self._normalize_field_state(time_payload.get("state"))
        item["time"] = time_payload
        location_payload = dict(item.get("location") or {})
        location_payload["state"] = self._normalize_field_state(location_payload.get("state"))
        item["location"] = location_payload
        item["state"] = self._normalize_fact_state(item.get("state"))
        if item.get("event_type") == "籍贯":
            item["time"] = {"state": "not_applicable"}
        item["evidence"] = self._materialize_evidence(context, item.get("evidence"), prefix=f"{item['key']}-x")
        return item

    def _normalize_historical_event(
        self,
        context: str,
        raw: dict[str, Any],
        key: str,
    ) -> dict[str, Any]:
        item = dict(raw)
        item["key"] = key
        item["state"] = self._normalize_fact_state(item.get("state"))
        item["evidence"] = self._materialize_evidence(context, item.get("evidence"), prefix=f"{item['key']}-x")
        return item

    @staticmethod
    def _normalize_field_state(value: Any) -> str:
        value = str(value or "not_mentioned")
        if value in {"present", "not_mentioned", "not_applicable", "uncertain", "unsupported"}:
            return value
        if value in {"confirmed", "has_fact", "mentioned", "available"}:
            return "present"
        return "uncertain" if value in {"unknown", "unclear"} else "not_mentioned"

    @staticmethod
    def _normalize_fact_state(value: Any) -> str:
        value = str(value or "confirmed")
        if value in {"confirmed", "uncertain", "unsupported"}:
            return value
        return "confirmed" if value in {"has_fact", "present", "mentioned"} else "uncertain"

    @staticmethod
    def _normalize_check_state(value: Any) -> str:
        value = str(value or "not_mentioned")
        if value in {"unreviewed", "has_fact", "not_mentioned", "uncertain", "unsupported"}:
            return value
        if value in {"confirmed", "present", "mentioned"}:
            return "has_fact"
        return "uncertain" if value in {"unknown", "unclear"} else "not_mentioned"

    @staticmethod
    def _spans_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
        return left_start < right_end and right_start < left_end

    @classmethod
    def _find_unoccupied_span(
        cls,
        context: str,
        quote: str,
        occupied: list[tuple[int, int]],
        *,
        prefer: tuple[int, int] | None,
    ) -> tuple[int, int] | None:
        if prefer is not None:
            start, end = prefer
            if (
                0 <= start < end <= len(context)
                and context[start:end] == quote
                and not any(
                    cls._spans_overlap(start, end, occupied_start, occupied_end)
                    for occupied_start, occupied_end in occupied
                )
            ):
                return start, end
            cursor = max(start + 1, 0)
            allow_wrap = True
        else:
            cursor = 0
            allow_wrap = False
        wrapped = False
        while True:
            index = context.find(quote, cursor)
            if index < 0:
                if allow_wrap and not wrapped:
                    wrapped = True
                    cursor = 0
                    continue
                return None
            end = index + len(quote)
            if not any(
                cls._spans_overlap(index, end, occupied_start, occupied_end)
                for occupied_start, occupied_end in occupied
            ):
                return index, end
            cursor = index + 1

    def _materialize_person_mentions(
        self,
        context: str,
        raw: Any,
        *,
        prefix: str,
    ) -> list[dict[str, Any]]:
        """同一人物 mentions 从长到短匹配，已占用区间不再给后续短称呼。"""

        candidates: list[tuple[int, int, str, tuple[int, int] | None]] = []
        if not isinstance(raw, list):
            return []
        for original_index, item in enumerate(raw[:50]):
            if isinstance(item, EvidenceSpan):
                item = item.model_dump(mode="python")
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "")
            if not quote:
                continue
            start = item.get("start")
            end = item.get("end")
            prefer: tuple[int, int] | None = None
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(context)
                and context[start:end] == quote
            ):
                prefer = (start, end)
            candidates.append((len(quote), original_index, quote, prefer))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        occupied: list[tuple[int, int]] = []
        placed: list[tuple[int, dict[str, Any]]] = []
        for _length, original_index, quote, prefer in candidates:
            span = self._find_unoccupied_span(context, quote, occupied, prefer=prefer)
            if span is None:
                continue
            start, end = span
            occupied.append((start, end))
            placed.append(
                (
                    original_index,
                    {
                        "source": "context",
                        "quote": quote,
                        "start": start,
                        "end": end,
                        "start_utf16": codepoint_to_utf16_offset(context, start),
                        "end_utf16": codepoint_to_utf16_offset(context, end),
                    },
                )
            )

        placed.sort(key=lambda item: (item[1]["start"], item[0]))
        result: list[dict[str, Any]] = []
        for index, (_original_index, item) in enumerate(placed, start=1):
            item["id"] = f"{prefix}{index}"
            result.append(item)
        return result

    @staticmethod
    def _materialize_evidence(
        context: str,
        raw: Any,
        *,
        prefix: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return result
        for index, item in enumerate(raw[:8], start=1):
            if isinstance(item, EvidenceSpan):
                # Repair 之前的结果通常已经被 Pydantic 转成 EvidenceSpan；
                # 仍需回到 quote 校验路径，才能自动重算错误的偏移量。
                item = item.model_dump(mode="python")
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "")
            if not quote:
                continue
            start = item.get("start")
            end = item.get("end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or context[start:end] != quote
            ):
                start = context.find(quote)
                end = start + len(quote) if start >= 0 else -1
            if start < 0 or end <= start or context[start:end] != quote:
                continue
            result.append(
                {
                    "id": f"{prefix}{index}",
                    "source": "context",
                    "quote": quote,
                    "start": start,
                    "end": end,
                    "start_utf16": codepoint_to_utf16_offset(context, start),
                    "end_utf16": codepoint_to_utf16_offset(context, end),
                }
            )
        return result

    def _load_server_format_examples(
        self,
        task_id: int,
        *,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """读取服务器已有金标/提交示例，只作为格式样例，不作为事实来源。"""

        examples: list[dict[str, Any]] = []
        used_task_ids: set[int] = set()

        gold_rows = (
            self.db.query(ExtractionGoldVersion)
            .filter(ExtractionGoldVersion.task_id != task_id)
            .order_by(ExtractionGoldVersion.locked_at.desc())
            .limit(limit)
            .all()
        )
        for row in gold_rows:
            label = self._parse_example_label(row.gold_json)
            if label is None or row.task_id in used_task_ids:
                continue
            examples.append(
                {
                    "source": "server_locked_gold",
                    "task_id": row.task_id,
                    "version": row.version,
                    "label": label,
                }
            )
            used_task_ids.add(row.task_id)
            if len(examples) >= limit:
                return examples

        remaining = limit - len(examples)
        if remaining > 0:
            submission_rows = (
                self.db.query(ExtractionAnnotationSubmission)
                .filter(
                    ExtractionAnnotationSubmission.task_id != task_id,
                    ExtractionAnnotationSubmission.state == "submitted",
                )
                .order_by(ExtractionAnnotationSubmission.updated_at.desc())
                .limit(remaining + len(used_task_ids))
                .all()
            )
            for row in submission_rows:
                label = self._parse_example_label(row.label_json)
                if label is None or row.task_id in used_task_ids:
                    continue
                examples.append(
                    {
                        "source": "server_submitted_annotation",
                        "task_id": row.task_id,
                        "slot_no": row.slot_no,
                        "label": label,
                    }
                )
                used_task_ids.add(row.task_id)
                if len(examples) >= limit:
                    break

        return examples

    @staticmethod
    def _parse_example_label(raw: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw or "{}")
            if isinstance(payload, dict) and isinstance(payload.get("label"), dict):
                payload = payload["label"]
            example = ExtractionAnnotationLabel.model_validate(payload).model_dump(mode="json")
            # 人工步骤确认与缺漏清单不是模型抽取目标，避免示例诱导复制。
            example.pop("step_reviews", None)
            example.pop("omissions", None)
            return example
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
            return None

    def _get_task_and_passage(self, task_id: int) -> tuple[ExtractionAnnotationTask, Passage]:
        task = self.db.get(ExtractionAnnotationTask, task_id)
        if task is None:
            raise AIAnnotationServiceError("未找到指定的标注任务", status_code=404)
        if task.status == "archived":
            raise AIAnnotationServiceError("归档任务不能继续生成 AI 标注", status_code=409)

        passage = self.db.get(Passage, task.passage_id)
        if passage is None:
            raise AIAnnotationServiceError("标注任务对应的古籍不存在", status_code=404)
        if task.context_sha256 != context_sha256(passage.context):
            raise AIAnnotationServiceError(
                "标注任务对应的古籍正文已发生变化，请先重新建立任务",
                status_code=409,
            )
        return task, passage

    @staticmethod
    def _canonical_provider_name(raw: str) -> str:
        """内置名称归一成固定代号，自定义名称保留显示文本。"""

        if any(ord(char) < 32 for char in raw):
            raise AIAnnotationConfigError("Provider 名称不能包含控制字符。")
        text = " ".join(raw.split())
        if not text:
            return ""
        if len(text) > 64:
            raise AIAnnotationConfigError("Provider 名称不能超过 64 个字符。")
        lowered = text.lower()
        aliases = {
            "openai-compatible": "openai_compatible",
            "openai compatible": "openai_compatible",
        }
        if lowered in aliases:
            return aliases[lowered]
        if lowered in {"mock", "deepseek", "openai_compatible"}:
            return lowered
        return text

    @staticmethod
    def _resolve_provider_config(config: AIAnnotationConfigRequest) -> tuple[str, str]:
        settings = get_settings()
        provider_name = ExtractionAIAnnotationService._canonical_provider_name(config.provider)
        if not provider_name:
            provider_name = (
                ExtractionAIAnnotationService._canonical_provider_name(settings.llm_provider)
                or "mock"
            )

        model_name = config.model.strip() or settings.llm_model_name.strip()
        if provider_name != "mock" and not model_name:
            raise AIAnnotationConfigError("请填写模型名称。")
        return provider_name, model_name

    @staticmethod
    def _build_messages(
        task: ExtractionAnnotationTask,
        passage: Passage,
        config: AIAnnotationConfigRequest,
        *,
        prompt_overrides: AIAnnotationPromptOverrides | None = None,
    ) -> list[dict[str, str]]:
        if prompt_overrides is not None:
            return [
                {"role": "system", "content": prompt_overrides.system_prompt},
                {"role": "user", "content": prompt_overrides.user_prompt},
            ]

        user_payload: dict[str, Any] = {
            "task_id": task.id,
            "spec_version": task.spec_version,
            "passage_id": passage.doc_id,
            "title": passage.title,
            "context_sha256": task.context_sha256,
            "context": passage.context,
        }
        if config.extra_instruction.strip():
            user_payload["extra_instruction"] = config.extra_instruction.strip()
        return [
            {"role": "system", "content": AI_ANNOTATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ]

    @staticmethod
    def _mock_content(task: ExtractionAnnotationTask, passage: Passage) -> str:
        """生成可离线验证链路的最小合法示例。"""

        context = passage.context
        if context:
            end = min(2, len(context))
            quote = context[:end]
            evidence = EvidenceSpan(
                id="mock-person-mention",
                quote=quote,
                start=0,
                end=end,
                start_utf16=0,
                end_utf16=codepoint_to_utf16_offset(context, end),
            )
            persons = [
                PersonAnnotation(
                    key="mock-person-1",
                    name_surface=quote,
                    level=1,
                    level_reason="离线 Mock 输出，仅用于检查标注 JSON 链路，需人工复核。",
                    mentions=[evidence],
                    event_checks={event_type: "not_mentioned" for event_type in EVENT_TYPES},
                )
            ]
        else:
            persons = []

        label = ExtractionAnnotationLabel(
            schema_version=task.spec_version,
            passage=PassageAnnotation(),
            persons=persons,
        )
        return json.dumps(label.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def _validate_content(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        content: str,
        *,
        source: str,
        provider: str,
        model: str,
        elapsed_ms: int,
        fragment_count: int = 0,
        truncated_fragments: list[str] | None = None,
        fragment_warnings: list[str] | None = None,
        token_usage: AIAnnotationTokenUsage | None = None,
    ) -> AIAnnotationResultResponse:
        format_issues: list[AIAnnotationValidationIssue] = []
        rule_issues: list[AIAnnotationValidationIssue] = []
        label: ExtractionAnnotationLabel | None = None
        document: dict[str, Any] | None = None

        try:
            document = _parse_json_content(content)
        except ValueError as exc:
            format_issues.append(_issue("root", str(exc), "invalid_json"))

        if document is not None:
            self._validate_wrapper_metadata(document, task, passage, format_issues)
            label_payload = document.get("label")
            if label_payload is None and "schema_version" in document:
                label_payload = document
            if not isinstance(label_payload, dict):
                format_issues.append(
                    _issue("label", "标注结果必须包含 label 对象，或直接使用标注对象作为根节点。", "label_required")
                )
            else:
                try:
                    label, schema_issues = load_model_keeping_original(
                        ExtractionAnnotationLabel,
                        label_payload,
                    )
                    drop_meaningless_excluded_mentions(label)
                    rule_issues.extend(schema_issues)
                except ValidationError as exc:
                    format_issues.extend(_validation_issues_from_pydantic(exc))

        export_document: AIAnnotationExportDocument | None = None
        if label is not None:
            rule_issues.extend(
                AIAnnotationValidationIssue(**issue)
                for issue in validate_for_submission(
                    label,
                    passage.context,
                    task.spec_version,
                    self.db,
                    require_step_reviews=False,
                )
            )
            export_document = AIAnnotationExportDocument(
                metadata=AIAnnotationExportMetadata(
                    task_id=task.id,
                    spec_version=task.spec_version,
                    context_sha256=task.context_sha256,
                ),
                passage=AIAnnotationExportPassage(
                    doc_id=passage.doc_id,
                    title=passage.title,
                    context=passage.context,
                    context_sha256=task.context_sha256,
                ),
                label=label,
            )

        if format_issues:
            status = "invalid_format"
        elif rule_issues:
            status = "invalid_rules"
        else:
            status = "valid"

        return AIAnnotationResultResponse(
            task_id=task.id,
            passage_id=passage.doc_id,
            passage_title=passage.title,
            spec_version=task.spec_version,
            context_sha256=task.context_sha256,
            source=source,  # type: ignore[arg-type]
            provider=provider,
            model=model,
            raw_content=content,
            document=export_document,
            label=label,
            validation_status=status,  # type: ignore[arg-type]
            validation_issues=[*format_issues, *rule_issues],
            elapsed_ms=elapsed_ms,
            fragment_count=fragment_count,
            truncated_fragments=truncated_fragments or [],
            fragment_warnings=fragment_warnings or [],
            token_usage=token_usage or AIAnnotationTokenUsage(),
        )

    @staticmethod
    def _validate_wrapper_metadata(
        document: dict[str, Any],
        task: ExtractionAnnotationTask,
        passage: Passage,
        issues: list[AIAnnotationValidationIssue],
    ) -> None:
        metadata = document.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            issues.append(_issue("metadata", "metadata 必须是对象。", "invalid_metadata"))
        elif isinstance(metadata, dict):
            if metadata.get("task_id") is not None and str(metadata.get("task_id")) != str(task.id):
                issues.append(_issue("metadata.task_id", "标注结果不属于当前任务。", "task_id_mismatch"))
            if metadata.get("spec_version") is not None and metadata.get("spec_version") != task.spec_version:
                issues.append(_issue("metadata.spec_version", "标注规范版本与当前任务不一致。", "schema_version_mismatch"))
            if metadata.get("context_sha256") is not None and metadata.get("context_sha256") != task.context_sha256:
                issues.append(_issue("metadata.context_sha256", "正文摘要与当前任务不一致。", "context_digest_mismatch"))

        passage_payload = document.get("passage")
        if passage_payload is not None and not isinstance(passage_payload, dict):
            issues.append(_issue("passage", "passage 必须是对象。", "invalid_passage"))
        elif isinstance(passage_payload, dict):
            if passage_payload.get("doc_id") is not None and str(passage_payload.get("doc_id")) != str(passage.doc_id):
                issues.append(_issue("passage.doc_id", "标注结果不属于当前篇目。", "passage_id_mismatch"))
            if passage_payload.get("context") is not None and passage_payload.get("context") != passage.context:
                issues.append(_issue("passage.context", "标注结果中的正文与当前篇目不一致。", "context_mismatch"))
            if passage_payload.get("context_sha256") is not None and passage_payload.get("context_sha256") != task.context_sha256:
                issues.append(_issue("passage.context_sha256", "标注结果中的正文摘要与当前篇目不一致。", "context_digest_mismatch"))
