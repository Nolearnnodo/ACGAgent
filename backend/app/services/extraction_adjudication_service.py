"""功能 A 双盲差异裁定、金标锁定与导出服务。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.extraction_annotation import (
    ExtractionAdjudicationDraft,
    ExtractionAnnotationSubmission,
    ExtractionAnnotationTask,
    ExtractionGoldVersion,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AdjudicationDifference,
    AdjudicationResolution,
    AdjudicationSubmissionResponse,
    AnnotationPassageResponse,
    ExtractionAdjudicationDetailResponse,
    ExtractionAdjudicationDraftResponse,
    ExtractionAdjudicationSummaryResponse,
    ExtractionAdjudicationUpdateRequest,
    ExtractionAnnotationLabel,
    ExtractionGoldVersionResponse,
)
from app.services.extraction_annotation_service import (
    AnnotationConflictError,
    AnnotationNotFoundError,
    AnnotationValidationError,
    context_sha256,
    validate_for_submission,
)


PERSON_FIELDS: tuple[tuple[str, str], ...] = (
    ("name_surface", "原文姓名"),
    ("completed_name", "补全姓名"),
    ("completion_reason", "补全依据"),
    ("courtesy_name", "字"),
    ("hao", "号"),
    ("titles", "别称与称谓"),
    ("level", "人物等级"),
    ("level_reason", "分级理由"),
    ("mentions", "人物 mention"),
    ("event_checks", "五类事件检查"),
    ("life_events", "生平事件"),
    ("historical_events", "历史事件关联"),
)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_label_json(value: str) -> ExtractionAnnotationLabel:
    try:
        return ExtractionAnnotationLabel.model_validate(json.loads(value or "{}"))
    except (json.JSONDecodeError, ValidationError, ValueError):
        return ExtractionAnnotationLabel()


def _semantic(value: Any) -> Any:
    """去掉本地稳定键后比较语义，避免双盲随机键制造伪差异。"""

    if isinstance(value, dict):
        return {
            key: _semantic(item)
            for key, item in value.items()
            if key not in {"key", "id"}
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _same(left: Any, right: Any) -> bool:
    return _semantic(left) == _semantic(right)


def _person_name(person: dict[str, Any]) -> str:
    raw = person.get("completed_name") or person.get("name_surface") or ""
    return re.sub(r"\s+", "", str(raw))


def _first_mention_start(person: dict[str, Any]) -> int | None:
    mentions = person.get("mentions") or []
    starts = [item.get("start") for item in mentions if isinstance(item, dict)]
    numeric = [item for item in starts if isinstance(item, int)]
    return min(numeric) if numeric else None


def _match_people(
    people_a: list[dict[str, Any]],
    people_b: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """先按姓名和首 mention 对齐，再按姓名就近匹配。"""

    unused_b = set(range(len(people_b)))
    matches: list[tuple[int, int]] = []
    unmatched_a: list[int] = []
    for index_a, person_a in enumerate(people_a):
        name = _person_name(person_a)
        candidates = [
            index_b
            for index_b in unused_b
            if name and _person_name(people_b[index_b]) == name
        ]
        if not candidates:
            unmatched_a.append(index_a)
            continue
        start_a = _first_mention_start(person_a)
        index_b = min(
            candidates,
            key=lambda candidate: (
                0 if _first_mention_start(people_b[candidate]) == start_a else 1,
                abs((_first_mention_start(people_b[candidate]) or 0) - (start_a or 0)),
                candidate,
            ),
        )
        unused_b.remove(index_b)
        matches.append((index_a, index_b))
    return matches, unmatched_a, sorted(unused_b)


def _difference_id(
    category: str,
    path: str,
    operation: str,
    value_a: Any,
    value_b: Any,
) -> str:
    payload = _json_dump([category, path, operation, _semantic(value_a), _semantic(value_b)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _difference(
    *,
    category: str,
    label: str,
    path: str,
    operation: str,
    value_a: Any,
    value_b: Any,
) -> AdjudicationDifference:
    return AdjudicationDifference(
        id=_difference_id(category, path, operation, value_a, value_b),
        category=category,
        label=label,
        path=path,
        operation=operation,
        value_a=value_a,
        value_b=value_b,
    )


def build_adjudication_differences(
    label_a: ExtractionAnnotationLabel,
    label_b: ExtractionAnnotationLabel | None,
) -> list[AdjudicationDifference]:
    """构造对人工可读且可重放的字段/事实组差异。"""

    if label_b is None:
        return []

    raw_a = label_a.model_dump(mode="json")
    raw_b = label_b.model_dump(mode="json")
    differences: list[AdjudicationDifference] = []

    for field, value_a in raw_a["passage"].items():
        value_b = raw_b["passage"].get(field)
        if not _same(value_a, value_b):
            differences.append(
                _difference(
                    category="passage",
                    label=f"文献字段 · {field}",
                    path=f"passage.{field}",
                    operation="replace",
                    value_a=value_a,
                    value_b=value_b,
                )
            )

    people_a: list[dict[str, Any]] = raw_a["persons"]
    people_b: list[dict[str, Any]] = copy.deepcopy(raw_b["persons"])
    matches, unmatched_a, unmatched_b = _match_people(people_a, people_b)
    key_map = {
        people_b[index_b]["key"]: people_a[index_a]["key"]
        for index_a, index_b in matches
    }
    for person in people_b:
        person["key"] = key_map.get(person["key"], person["key"])

    for index_a, index_b in matches:
        person_a = people_a[index_a]
        person_b = people_b[index_b]
        display_name = person_a.get("completed_name") or person_a.get("name_surface") or f"人物 {index_a + 1}"
        for field, field_label in PERSON_FIELDS:
            value_a = person_a.get(field)
            value_b = person_b.get(field)
            if not _same(value_a, value_b):
                differences.append(
                    _difference(
                        category="person",
                        label=f"{display_name} · {field_label}",
                        path=f"persons.{index_a}.{field}",
                        operation="replace",
                        value_a=value_a,
                        value_b=value_b,
                    )
                )

    for index_a in unmatched_a:
        person_a = people_a[index_a]
        display_name = person_a.get("completed_name") or person_a.get("name_surface") or f"人物 {index_a + 1}"
        differences.append(
            _difference(
                category="person",
                label=f"仅 A 标注人物 · {display_name}",
                path=f"persons.{index_a}",
                operation="remove",
                value_a=person_a,
                value_b=None,
            )
        )

    for index_b in unmatched_b:
        person_b = people_b[index_b]
        display_name = person_b.get("completed_name") or person_b.get("name_surface") or f"人物 B{index_b + 1}"
        differences.append(
            _difference(
                category="person",
                label=f"仅 B 标注人物 · {display_name}",
                path="persons",
                operation="add",
                value_a=None,
                value_b=person_b,
            )
        )

    relations_b = copy.deepcopy(raw_b["person_relations"])
    for relation in relations_b:
        relation["source_person_key"] = key_map.get(
            relation["source_person_key"], relation["source_person_key"]
        )
        relation["target_person_key"] = key_map.get(
            relation["target_person_key"], relation["target_person_key"]
        )
    collection_fields = (
        ("person_relations", "relation", "人物关系", raw_a["person_relations"], relations_b),
        ("excluded_mentions", "review", "排除称谓", raw_a["excluded_mentions"], raw_b["excluded_mentions"]),
        ("unresolved_items", "review", "暂不能判断项", raw_a["unresolved_items"], raw_b["unresolved_items"]),
        ("schema_conflicts", "review", "Schema 冲突", raw_a["schema_conflicts"], raw_b["schema_conflicts"]),
    )
    for path, category, label, value_a, value_b in collection_fields:
        if not _same(value_a, value_b):
            differences.append(
                _difference(
                    category=category,
                    label=label,
                    path=path,
                    operation="replace",
                    value_a=value_a,
                    value_b=value_b,
                )
            )
    return differences


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target: Any = root
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    final = parts[-1]
    if isinstance(target, list):
        target[int(final)] = copy.deepcopy(value)
    else:
        target[final] = copy.deepcopy(value)


def apply_adjudication_resolutions(
    label_a: ExtractionAnnotationLabel,
    differences: list[AdjudicationDifference],
    resolutions: list[AdjudicationResolution],
) -> ExtractionAnnotationLabel:
    """从 A 版和差异决策确定性重建候选金标。"""

    resolution_map = {item.difference_id: item for item in resolutions}
    if len(resolution_map) != len(resolutions):
        raise AnnotationValidationError("同一差异不能重复裁定。")
    difference_ids = {item.id for item in differences}
    unknown = set(resolution_map) - difference_ids
    if unknown:
        raise AnnotationValidationError("裁定草稿包含已经失效的差异，请刷新后重试。")

    result = label_a.model_dump(mode="json")
    removals: list[tuple[int, AdjudicationDifference, AdjudicationResolution]] = []
    for difference in differences:
        resolution = resolution_map.get(difference.id)
        if resolution is None or resolution.decision == "a":
            continue
        chosen = difference.value_b if resolution.decision == "b" else resolution.manual_value
        if resolution.decision == "manual" and not resolution.note.strip():
            raise AnnotationValidationError(f"手工裁定“{difference.label}”时必须填写说明。")
        if difference.operation == "replace":
            _set_path(result, difference.path, chosen)
        elif difference.operation == "add":
            target = result[difference.path]
            target.append(copy.deepcopy(chosen))
        elif difference.operation == "remove":
            index = int(difference.path.rsplit(".", 1)[1])
            if resolution.decision == "manual":
                _set_path(result, difference.path, chosen)
            else:
                removals.append((index, difference, resolution))

    for index, _, _ in sorted(removals, key=lambda item: item[0], reverse=True):
        del result["persons"][index]
    try:
        return ExtractionAnnotationLabel.model_validate(result)
    except ValidationError as exc:
        details = [
            {
                "path": ".".join(str(part) for part in issue["loc"]),
                "message": issue["msg"],
                "code": "invalid_manual_adjudication",
            }
            for issue in exc.errors()
        ]
        raise AnnotationValidationError(details) from exc


class ExtractionAdjudicationService:
    def __init__(self, db: Session):
        self.db = db

    def list_adjudications(self) -> list[ExtractionAdjudicationSummaryResponse]:
        tasks = (
            self.db.query(ExtractionAnnotationTask)
            .filter(ExtractionAnnotationTask.status.in_(("ready_for_adjudication", "completed")))
            .order_by(
                ExtractionAnnotationTask.status.asc(),
                ExtractionAnnotationTask.priority.desc(),
                ExtractionAnnotationTask.updated_at.desc(),
            )
            .all()
        )
        result: list[ExtractionAdjudicationSummaryResponse] = []
        for task in tasks:
            passage = self.db.get(Passage, task.passage_id)
            if passage is None:
                continue
            submissions = self._submitted(task.id)
            label_a, label_b = self._candidate_labels(submissions)
            differences = build_adjudication_differences(label_a, label_b)
            draft = self._draft(task.id)
            resolutions = self._load_resolutions(draft.resolutions_json) if draft else []
            latest = self._latest_gold(task.id)
            result.append(
                ExtractionAdjudicationSummaryResponse(
                    task_id=task.id,
                    passage_id=passage.doc_id,
                    passage_title=passage.title,
                    task_status=task.status,
                    submitted_count=len(submissions),
                    difference_count=len(differences),
                    resolved_count=len({item.difference_id for item in resolutions}),
                    latest_gold_version=latest.version if latest else None,
                    updated_at=task.updated_at,
                )
            )
        return result

    def get_adjudication(self, task_id: int) -> ExtractionAdjudicationDetailResponse:
        task, passage, submissions = self._require_adjudicable(task_id)
        return self._build_detail(task, passage, submissions)

    def save_draft(
        self,
        task_id: int,
        payload: ExtractionAdjudicationUpdateRequest,
        reviewer: User,
    ) -> ExtractionAdjudicationDetailResponse:
        task, passage, submissions = self._require_adjudicable(task_id)
        self._assert_current_context(task, passage)
        draft = self._draft(task_id)
        self._assert_draft_revision(draft, payload.revision)
        label_a, label_b = self._candidate_labels(submissions)
        differences = build_adjudication_differences(label_a, label_b)
        gold = payload.label_override or apply_adjudication_resolutions(
            label_a, differences, payload.resolutions
        )
        self._upsert_draft(draft, task_id, reviewer, payload, gold)
        self.db.commit()
        return self._build_detail(task, passage, submissions)

    def lock_gold(
        self,
        task_id: int,
        payload: ExtractionAdjudicationUpdateRequest,
        reviewer: User,
    ) -> ExtractionAdjudicationDetailResponse:
        task, passage, submissions = self._require_adjudicable(task_id)
        self._assert_current_context(task, passage)
        draft = self._draft(task_id)
        self._assert_draft_revision(draft, payload.revision)
        label_a, label_b = self._candidate_labels(submissions)
        differences = build_adjudication_differences(label_a, label_b)
        resolution_map = {item.difference_id: item for item in payload.resolutions}
        unresolved = [item.label for item in differences if item.id not in resolution_map]
        if unresolved:
            preview = "、".join(unresolved[:3])
            suffix = "等" if len(unresolved) > 3 else ""
            raise AnnotationValidationError(f"仍有 {len(unresolved)} 项差异未裁定：{preview}{suffix}。")

        latest = self._latest_gold(task_id)
        if latest is not None and not payload.change_reason.strip():
            raise AnnotationValidationError("建立新版金标时必须填写修订原因。")
        gold = payload.label_override or apply_adjudication_resolutions(
            label_a, differences, payload.resolutions
        )
        issues = validate_for_submission(gold, passage.context, task.spec_version, self.db)
        if issues:
            raise AnnotationValidationError(issues)

        draft = self._upsert_draft(draft, task_id, reviewer, payload, gold)
        now = datetime.now(timezone.utc)
        next_version = (latest.version if latest else 0) + 1
        source_ids = [item.id for item in submissions]
        log = {
            "task_id": task.id,
            "version": next_version,
            "reviewer_id": reviewer.id,
            "locked_at": now.isoformat(),
            "change_reason": payload.change_reason,
            "label_override_used": payload.label_override is not None,
            "resolutions": [
                self._resolution_log(item, resolution_map[item.id])
                for item in differences
            ],
        }
        gold_version = ExtractionGoldVersion(
            task_id=task.id,
            version=next_version,
            gold_json=_json_dump(gold.model_dump(mode="json")),
            source_submission_ids_json=_json_dump(source_ids),
            adjudication_log_json=_json_dump(log),
            reviewer_id=reviewer.id,
            locked_at=now,
        )
        task.status = "completed"
        self.db.add_all([draft, gold_version, task])
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AnnotationConflictError("金标版本刚刚被其他复核员更新，请刷新后重试。") from exc
        return self._build_detail(task, passage, submissions)

    def export_gold_yaml(self, task_id: int, version: int | None = None) -> tuple[str, str]:
        task = self.db.get(ExtractionAnnotationTask, task_id)
        if task is None:
            raise AnnotationNotFoundError("未找到标注任务。")
        passage = self.db.get(Passage, task.passage_id)
        if passage is None:
            raise AnnotationNotFoundError("标注任务对应的古籍不存在。")
        query = self.db.query(ExtractionGoldVersion).filter(
            ExtractionGoldVersion.task_id == task_id
        )
        if version is not None:
            query = query.filter(ExtractionGoldVersion.version == version)
        gold = query.order_by(ExtractionGoldVersion.version.desc()).first()
        if gold is None:
            raise AnnotationNotFoundError("该任务尚无可导出的锁定金标。")
        label = _load_label_json(gold.gold_json)
        payload = {
            "metadata": {
                "task_id": task.id,
                "gold_version": gold.version,
                "spec_version": task.spec_version,
                "context_sha256": task.context_sha256,
                "source_submission_ids": json.loads(gold.source_submission_ids_json),
                "reviewer_id": gold.reviewer_id,
                "locked_at": gold.locked_at.isoformat(),
            },
            "passage": {
                "doc_id": passage.doc_id,
                "title": passage.title,
                "source_type": passage.source_type,
                "context": passage.context,
            },
            "label": label.model_dump(mode="json"),
        }
        content = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        return f"extraction-gold-task-{task.id}-v{gold.version}.yaml", content

    def _require_adjudicable(
        self, task_id: int
    ) -> tuple[ExtractionAnnotationTask, Passage, list[ExtractionAnnotationSubmission]]:
        task = self.db.get(ExtractionAnnotationTask, task_id)
        if task is None:
            raise AnnotationNotFoundError("未找到标注任务。")
        passage = self.db.get(Passage, task.passage_id)
        if passage is None:
            raise AnnotationNotFoundError("标注任务对应的古籍不存在。")
        submissions = self._submitted(task_id)
        if len(submissions) < task.required_annotation_count:
            raise AnnotationConflictError("盲标尚未全部提交，当前不能进入裁定。")
        return task, passage, submissions

    def _submitted(self, task_id: int) -> list[ExtractionAnnotationSubmission]:
        return (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(
                ExtractionAnnotationSubmission.task_id == task_id,
                ExtractionAnnotationSubmission.state == "submitted",
            )
            .order_by(ExtractionAnnotationSubmission.slot_no.asc())
            .all()
        )

    @staticmethod
    def _candidate_labels(
        submissions: list[ExtractionAnnotationSubmission],
    ) -> tuple[ExtractionAnnotationLabel, ExtractionAnnotationLabel | None]:
        label_a = _load_label_json(submissions[0].label_json)
        label_b = _load_label_json(submissions[1].label_json) if len(submissions) > 1 else None
        return label_a, label_b

    def _draft(self, task_id: int) -> ExtractionAdjudicationDraft | None:
        return (
            self.db.query(ExtractionAdjudicationDraft)
            .filter(ExtractionAdjudicationDraft.task_id == task_id)
            .first()
        )

    def _latest_gold(self, task_id: int) -> ExtractionGoldVersion | None:
        return (
            self.db.query(ExtractionGoldVersion)
            .filter(ExtractionGoldVersion.task_id == task_id)
            .order_by(ExtractionGoldVersion.version.desc())
            .first()
        )

    @staticmethod
    def _assert_draft_revision(
        draft: ExtractionAdjudicationDraft | None,
        revision: int,
    ) -> None:
        current = draft.revision if draft else 0
        if current != revision:
            raise AnnotationConflictError(
                f"裁定草稿版本冲突：服务端 revision={current}，请刷新后再编辑。"
            )

    @staticmethod
    def _assert_current_context(task: ExtractionAnnotationTask, passage: Passage) -> None:
        if task.context_sha256 != context_sha256(passage.context):
            raise AnnotationConflictError("古籍正文已发生变化，该任务不能继续裁定。")

    def _upsert_draft(
        self,
        draft: ExtractionAdjudicationDraft | None,
        task_id: int,
        reviewer: User,
        payload: ExtractionAdjudicationUpdateRequest,
        gold: ExtractionAnnotationLabel,
    ) -> ExtractionAdjudicationDraft:
        if draft is None:
            draft = ExtractionAdjudicationDraft(
                task_id=task_id,
                reviewer_id=reviewer.id,
                revision=0,
            )
        draft.reviewer_id = reviewer.id
        draft.resolutions_json = _json_dump(
            [item.model_dump(mode="json") for item in payload.resolutions]
        )
        draft.gold_json = _json_dump(gold.model_dump(mode="json"))
        draft.change_reason = payload.change_reason
        draft.revision += 1
        self.db.add(draft)
        self.db.flush()
        return draft

    @staticmethod
    def _load_resolutions(raw: str) -> list[AdjudicationResolution]:
        try:
            return [AdjudicationResolution.model_validate(item) for item in json.loads(raw or "[]")]
        except (json.JSONDecodeError, ValidationError, TypeError):
            return []

    @staticmethod
    def _resolution_log(
        difference: AdjudicationDifference,
        resolution: AdjudicationResolution,
    ) -> dict[str, Any]:
        if resolution.decision == "a":
            final_value = difference.value_a
        elif resolution.decision == "b":
            final_value = difference.value_b
        else:
            final_value = resolution.manual_value
        return {
            "difference_id": difference.id,
            "category": difference.category,
            "label": difference.label,
            "path": difference.path,
            "operation": difference.operation,
            "decision": resolution.decision,
            "value_a": difference.value_a,
            "value_b": difference.value_b,
            "final_value": final_value,
            "note": resolution.note,
        }

    def _build_detail(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        submissions: list[ExtractionAnnotationSubmission],
    ) -> ExtractionAdjudicationDetailResponse:
        label_a, label_b = self._candidate_labels(submissions)
        differences = build_adjudication_differences(label_a, label_b)
        draft = self._draft(task.id)
        latest = self._latest_gold(task.id)
        if draft is not None:
            draft_response = ExtractionAdjudicationDraftResponse(
                id=draft.id,
                revision=draft.revision,
                resolutions=self._load_resolutions(draft.resolutions_json),
                gold_label=_load_label_json(draft.gold_json),
                change_reason=draft.change_reason,
                updated_at=draft.updated_at,
            )
        else:
            initial_gold = _load_label_json(latest.gold_json) if latest else label_a
            draft_response = ExtractionAdjudicationDraftResponse(
                id=None,
                revision=0,
                resolutions=[],
                gold_label=initial_gold,
                change_reason="",
                updated_at=None,
            )
        return ExtractionAdjudicationDetailResponse(
            task_id=task.id,
            task_status=task.status,
            spec_version=task.spec_version,
            context_sha256=task.context_sha256,
            passage=AnnotationPassageResponse(
                doc_id=passage.doc_id,
                title=passage.title,
                context=passage.context,
                source_type=passage.source_type,
                workflow_status=passage.workflow_status,
            ),
            submissions=[
                AdjudicationSubmissionResponse(
                    id=item.id,
                    slot_no=item.slot_no,
                    revision=item.revision,
                    submitted_at=item.submitted_at,
                    label=_load_label_json(item.label_json),
                )
                for item in submissions
                if item.submitted_at is not None
            ],
            differences=differences,
            draft=draft_response,
            latest_gold=self._build_gold_response(latest) if latest else None,
        )

    @staticmethod
    def _build_gold_response(gold: ExtractionGoldVersion) -> ExtractionGoldVersionResponse:
        try:
            log = json.loads(gold.adjudication_log_json)
        except json.JSONDecodeError:
            log = {}
        return ExtractionGoldVersionResponse(
            id=gold.id,
            task_id=gold.task_id,
            version=gold.version,
            label=_load_label_json(gold.gold_json),
            source_submission_ids=json.loads(gold.source_submission_ids_json),
            reviewer_id=gold.reviewer_id,
            change_reason=str(log.get("change_reason", "")),
            locked_at=gold.locked_at,
        )
