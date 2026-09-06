"""功能 A 人工标注任务、盲标草稿与提交服务。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from itertools import product
from typing import Iterable

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
from app.models.extraction_ai_annotation import ExtractionAIAnnotationJob
from app.models.dictionary import EraDictionary, HistoricalEventDictionary
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    EVENT_TYPES,
    AnnotationPassageResponse,
    EvidenceSpan,
    ExtractionAnnotationLabel,
    ExtractionSubmissionResponse,
    ExtractionTaskAssignmentResponse,
    ExtractionTaskCreateRequest,
    ExtractionTaskDetailResponse,
    ExtractionTaskSummaryResponse,
)


logger = logging.getLogger(__name__)


class ExtractionAnnotationServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str | list[dict[str, str]]):
        super().__init__(str(detail))
        self.detail = detail


class AnnotationNotFoundError(ExtractionAnnotationServiceError):
    status_code = 404


class AnnotationForbiddenError(ExtractionAnnotationServiceError):
    status_code = 403


class AnnotationConflictError(ExtractionAnnotationServiceError):
    status_code = 409


class AnnotationValidationError(ExtractionAnnotationServiceError):
    status_code = 422


def context_sha256(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()


def codepoint_to_utf16_offset(text: str, offset: int) -> int:
    """把 Python code point offset 转为 JavaScript/Java UTF-16 code unit offset。"""

    return len(text[:offset].encode("utf-16-le")) // 2


def _dump_label(label: ExtractionAnnotationLabel) -> str:
    return json.dumps(
        label.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_label(submission: ExtractionAnnotationSubmission) -> ExtractionAnnotationLabel:
    try:
        raw = json.loads(submission.label_json or "{}")
        return ExtractionAnnotationLabel.model_validate(raw)
    except (json.JSONDecodeError, ValueError):
        return ExtractionAnnotationLabel()


def _issue(path: str, message: str, code: str) -> dict[str, str]:
    return {"path": path, "message": message, "code": code}


ALLOWED_RELATION_INVERSES: dict[str, tuple[str, ...]] = {
    "F": ("S", "D"),
    "M": ("S", "D"),
    "S": ("F", "M"),
    "D": ("F", "M"),
    "H": ("W", "Z"),
    "W": ("H",),
    "Z": ("H",),
    "C": ("C",),
    "B": ("B",),
    "O": ("O",),
}


def relation_reverse_suggestions(codes: Iterable[str]) -> list[list[str]]:
    """按路径反转规则给出所有合法反向代码链，不猜测子女性别。"""

    chain = list(codes)
    if not chain or len(chain) > 3 or any(code not in ALLOWED_RELATION_INVERSES for code in chain):
        return []
    choices = [ALLOWED_RELATION_INVERSES[code] for code in reversed(chain)]
    return [list(candidate) for candidate in product(*choices)]


def are_inverse_relation_chains(codes: Iterable[str], reverse_codes: Iterable[str]) -> bool:
    reverse = list(reverse_codes)
    return reverse in relation_reverse_suggestions(codes)


def _iter_evidence(
    label: ExtractionAnnotationLabel,
) -> Iterable[tuple[str, EvidenceSpan]]:
    for person_index, person in enumerate(label.persons):
        for evidence_index, evidence in enumerate(person.mentions):
            yield f"persons.{person_index}.mentions.{evidence_index}", evidence
        for event_index, event in enumerate(person.life_events):
            for evidence_index, evidence in enumerate(event.evidence):
                yield (
                    f"persons.{person_index}.life_events.{event_index}.evidence.{evidence_index}",
                    evidence,
                )
        for event_index, event in enumerate(person.historical_events):
            for evidence_index, evidence in enumerate(event.evidence):
                yield (
                    f"persons.{person_index}.historical_events.{event_index}.evidence.{evidence_index}",
                    evidence,
                )
    for relation_index, relation in enumerate(label.person_relations):
        for evidence_index, evidence in enumerate(relation.evidence):
            yield f"person_relations.{relation_index}.evidence.{evidence_index}", evidence
    for item_name in ("excluded_mentions", "unresolved_items", "schema_conflicts"):
        for item_index, item in enumerate(getattr(label, item_name)):
            for evidence_index, evidence in enumerate(item.evidence):
                yield f"{item_name}.{item_index}.evidence.{evidence_index}", evidence


def validate_evidence_offsets(
    label: ExtractionAnnotationLabel,
    context: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path, evidence in _iter_evidence(label):
        if evidence.end > len(context):
            issues.append(_issue(path, "证据区间超出当前正文长度。", "evidence_out_of_range"))
            continue
        expected_quote = context[evidence.start : evidence.end]
        if expected_quote != evidence.quote:
            issues.append(
                _issue(path, "证据文字与当前正文对应位置不一致。", "evidence_quote_mismatch")
            )
        expected_start_utf16 = codepoint_to_utf16_offset(context, evidence.start)
        expected_end_utf16 = codepoint_to_utf16_offset(context, evidence.end)
        if (
            evidence.start_utf16 != expected_start_utf16
            or evidence.end_utf16 != expected_end_utf16
        ):
            issues.append(
                _issue(
                    path,
                    "证据的 UTF-16 偏移与 Unicode 字符偏移不一致。",
                    "evidence_utf16_mismatch",
                )
            )
    return issues


def discard_out_of_range_evidence(
    label: ExtractionAnnotationLabel,
    context: str,
) -> int:
    """丢弃超出正文字符范围的证据片段，保留对应的其他标注和约束问题。"""

    context_length = len(context)
    discarded = 0

    def filter_evidence(owner: object, field_name: str = "evidence") -> None:
        nonlocal discarded
        evidence = list(getattr(owner, field_name))
        kept = [
            item
            for item in evidence
            if 0 <= item.start < item.end <= context_length
        ]
        discarded += len(evidence) - len(kept)
        setattr(owner, field_name, kept)

    for person in label.persons:
        filter_evidence(person, "mentions")
        for event in person.life_events:
            filter_evidence(event)
        for event in person.historical_events:
            filter_evidence(event)
    for relation in label.person_relations:
        filter_evidence(relation)
    for item in (
        *label.excluded_mentions,
        *label.unresolved_items,
        *label.schema_conflicts,
    ):
        filter_evidence(item)
    return discarded


def validate_for_submission(
    label: ExtractionAnnotationLabel,
    context: str,
    spec_version: str,
    db: Session | None = None,
) -> list[dict[str, str]]:
    """执行提交级阻断校验；草稿只校验证据可回放。"""

    issues = validate_evidence_offsets(label, context)
    if label.schema_version != spec_version:
        issues.append(
            _issue("schema_version", "标签规范版本与任务版本不一致。", "schema_version_mismatch")
        )

    era_names: set[str] = set()
    historical_event_names: set[str] = set()
    if db is not None:
        era_rows = db.query(
            EraDictionary.era,
            EraDictionary.simplified,
            EraDictionary.traditional,
        ).all()
        era_names = {
            value
            for row in era_rows
            for value in row
            if isinstance(value, str) and value.strip()
        }
        historical_event_names = {
            row[0]
            for row in db.query(HistoricalEventDictionary.event_name).all()
            if row[0]
        }
    if label.passage.era.strip() and era_names and label.passage.era not in era_names:
        issues.append(_issue("passage.era", "文献年号不在当前年号字典中。", "era_outside_dictionary"))

    person_keys = [person.key for person in label.persons]
    person_key_set = set(person_keys)
    if len(person_keys) != len(person_key_set):
        issues.append(_issue("persons", "人物稳定键不能重复。", "duplicate_person_key"))
    if not label.persons:
        issues.append(_issue("persons", "至少需要建立一位人物。", "persons_required"))

    level_one_count = sum(person.level == 1 for person in label.persons)
    permits_multiple = any(
        conflict.code in {"multiple_protagonists", "material_unusable"}
        for conflict in label.schema_conflicts
    )
    if level_one_count != 1 and not permits_multiple:
        issues.append(
            _issue("persons", "普通文献必须有且仅有一位一级人物。", "invalid_level_one_count")
        )

    event_keys: set[str] = set()
    historical_event_keys: set[str] = set()
    person_by_key = {person.key: person for person in label.persons}
    for person_index, person in enumerate(label.persons):
        prefix = f"persons.{person_index}"
        if not person.name_surface.strip():
            issues.append(_issue(f"{prefix}.name_surface", "人物原文姓名不能为空。", "name_required"))
        if not person.level_reason.strip():
            issues.append(_issue(f"{prefix}.level_reason", "需要填写人物分级理由。", "level_reason_required"))
        if not person.mentions:
            issues.append(_issue(f"{prefix}.mentions", "人物至少需要一处原文 mention。", "mention_required"))

        if person.level == 3:
            if person.life_events or person.historical_events:
                issues.append(
                    _issue(prefix, "三级人物不能进入主评测事件。", "level_three_has_events")
                )
        else:
            for event_type in EVENT_TYPES:
                state = person.event_checks.get(event_type, "unreviewed")
                matching_events = [
                    event for event in person.life_events if event.event_type == event_type
                ]
                check_path = f"{prefix}.event_checks.{event_type}"
                if state == "unreviewed":
                    issues.append(
                        _issue(check_path, f"尚未检查{event_type}事件。", "event_check_required")
                    )
                elif state == "has_fact" and not matching_events:
                    issues.append(
                        _issue(check_path, f"已选择有{event_type}事实，但未建立事件。", "event_missing")
                    )
                elif matching_events and state != "has_fact":
                    issues.append(
                        _issue(check_path, f"已有{event_type}事件，检查状态应为有事实。", "event_check_mismatch")
                    )

        for event_index, event in enumerate(person.life_events):
            event_path = f"{prefix}.life_events.{event_index}"
            if event.key in event_keys:
                issues.append(_issue(f"{event_path}.key", "事件稳定键不能重复。", "duplicate_event_key"))
            event_keys.add(event.key)
            if not event.evidence:
                issues.append(_issue(f"{event_path}.evidence", "事件必须有原文证据。", "evidence_required"))
            if event.event_type == "籍贯":
                if event.time.state != "not_applicable" or any(
                    (
                        event.time.raw,
                        event.time.era,
                        event.time.era_year,
                        event.time.gregorian_year,
                        event.time.month_text,
                        event.time.day_text,
                    )
                ):
                    issues.append(
                        _issue(f"{event_path}.time", "籍贯事件的时间必须标为本项不适用。", "native_place_has_time")
                    )
            if event.event_type == "任职" and not event.official_title.strip():
                issues.append(
                    _issue(f"{event_path}.official_title", "任职事件必须填写官职。", "official_title_required")
                )
            if event.event_type != "任职" and event.official_title.strip():
                issues.append(
                    _issue(f"{event_path}.official_title", "非任职事件不能填写官职。", "unexpected_official_title")
                )
            if event.time.era.strip() and era_names and event.time.era not in era_names:
                issues.append(
                    _issue(
                        f"{event_path}.time.era",
                        "事件年号不在当前年号字典中。",
                        "era_outside_dictionary",
                    )
                )

        for event_index, event in enumerate(person.historical_events):
            event_path = f"{prefix}.historical_events.{event_index}"
            if event.key in historical_event_keys:
                issues.append(
                    _issue(f"{event_path}.key", "历史事件关联稳定键不能重复。", "duplicate_historical_event_key")
                )
            historical_event_keys.add(event.key)
            if not event.event_name.strip():
                issues.append(_issue(f"{event_path}.event_name", "历史事件名称不能为空。", "event_name_required"))
            if not event.relation_summary.strip():
                issues.append(
                    _issue(f"{event_path}.relation_summary", "需要填写人物与历史事件的关系说明。", "relation_summary_required")
                )
            if not event.evidence:
                issues.append(_issue(f"{event_path}.evidence", "历史事件关联必须有证据。", "evidence_required"))
            if (
                event.event_name.strip()
                and not event.outside_dictionary
                and historical_event_names
                and event.event_name not in historical_event_names
            ):
                issues.append(
                    _issue(
                        f"{event_path}.event_name",
                        "请选择历史事件字典中的名称，或明确标记为表外事件。",
                        "historical_event_outside_dictionary",
                    )
                )

    relation_keys: set[str] = set()
    for relation_index, relation in enumerate(label.person_relations):
        path = f"person_relations.{relation_index}"
        if relation.key in relation_keys:
            issues.append(_issue(f"{path}.key", "人物关系稳定键不能重复。", "duplicate_relation_key"))
        relation_keys.add(relation.key)
        if relation.source_person_key == relation.target_person_key:
            issues.append(_issue(path, "人物关系两端不能是同一人物。", "relation_self_loop"))
        for endpoint_name, endpoint in (
            ("source_person_key", relation.source_person_key),
            ("target_person_key", relation.target_person_key),
        ):
            if endpoint not in person_key_set:
                issues.append(_issue(f"{path}.{endpoint_name}", "人物关系引用了不存在的人物。", "unknown_person"))
            elif person_by_key[endpoint].level == 3:
                issues.append(_issue(f"{path}.{endpoint_name}", "主任务的人物关系不能使用三级人物。", "level_three_relation"))
        if not relation.codes or not relation.reverse_codes:
            issues.append(_issue(path, "人物关系必须填写正向和反向代码。", "relation_codes_required"))
        elif not are_inverse_relation_chains(relation.codes, relation.reverse_codes):
            issues.append(_issue(path, "正向与反向关系链不互逆。", "relation_inverse_mismatch"))
        if "O" in relation.codes and len(relation.codes) != 1:
            issues.append(_issue(f"{path}.codes", "O 不能与其他关系代码混用。", "invalid_other_relation"))
        if "O" in relation.reverse_codes and len(relation.reverse_codes) != 1:
            issues.append(_issue(f"{path}.reverse_codes", "O 不能与其他反向代码混用。", "invalid_other_relation"))
        if "O" in relation.codes and not relation.note.strip():
            issues.append(_issue(f"{path}.note", "其他关系必须说明正向含义。", "relation_note_required"))
        if "O" in relation.reverse_codes and not relation.reverse_note.strip():
            issues.append(_issue(f"{path}.reverse_note", "其他关系必须说明反向含义。", "relation_note_required"))
        if not relation.evidence:
            issues.append(_issue(f"{path}.evidence", "人物关系必须有原文证据。", "evidence_required"))

    for item_index, item in enumerate(label.excluded_mentions):
        path = f"excluded_mentions.{item_index}"
        if not item.reason.strip():
            issues.append(_issue(f"{path}.reason", "排除称谓必须填写原因。", "reason_required"))
        if not item.evidence:
            issues.append(_issue(f"{path}.evidence", "排除称谓必须保留原文位置。", "evidence_required"))
    for item_index, item in enumerate(label.unresolved_items):
        if not item.note.strip():
            issues.append(
                _issue(f"unresolved_items.{item_index}.note", "暂不能判断项必须填写说明。", "note_required")
            )
    for item_index, item in enumerate(label.schema_conflicts):
        if not item.code.strip() or not item.note.strip():
            issues.append(
                _issue(
                    f"schema_conflicts.{item_index}",
                    "Schema 冲突必须填写类型和说明。",
                    "schema_conflict_incomplete",
                )
            )
    return issues


def parse_imported_label(
    content: bytes | str,
    task: ExtractionAnnotationTask,
    passage: Passage,
    db: Session,
    *,
    require_submission: bool,
    allow_validation_issues: bool = False,
) -> ExtractionAnnotationLabel:
    """解析并校验上传的人工标注文件。

    文件可以是金标导出的完整 wrapper，也可以是直接的 ``label`` 对象。
    草稿导入只阻断正文证据和版本问题；金标导入还会执行完整提交校验。
    """

    try:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    except UnicodeDecodeError as exc:
        raise AnnotationValidationError("标注结果文件必须使用 UTF-8 编码。") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AnnotationValidationError("标注结果文件不是合法的 YAML/JSON。") from exc

    if not isinstance(document, dict):
        raise AnnotationValidationError("标注结果文件的根节点必须是对象。")

    metadata = document.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise AnnotationValidationError("标注结果 metadata 必须是对象。")

    declared_task_id = metadata.get("task_id")
    if declared_task_id is not None:
        try:
            declared_task_id = int(declared_task_id)
        except (TypeError, ValueError) as exc:
            raise AnnotationValidationError("标注结果 metadata.task_id 必须是整数。") from exc
        if declared_task_id != task.id:
            raise AnnotationValidationError("标注结果不属于当前标注任务。")

    declared_spec = metadata.get("spec_version")
    if declared_spec is not None and declared_spec != task.spec_version:
        raise AnnotationValidationError("标注结果规范版本与当前任务不一致。")

    declared_digest = metadata.get("context_sha256")
    if declared_digest is not None and declared_digest != context_sha256(passage.context):
        raise AnnotationConflictError("标注结果对应的正文版本已变化，不能导入当前任务。")

    passage_payload = document.get("passage")
    if passage_payload is not None:
        if not isinstance(passage_payload, dict):
            raise AnnotationValidationError("标注结果 passage 必须是对象。")
        doc_id = passage_payload.get("doc_id")
        if doc_id is not None:
            try:
                doc_id = int(doc_id)
            except (TypeError, ValueError) as exc:
                raise AnnotationValidationError("标注结果 passage.doc_id 必须是整数。") from exc
            if doc_id != passage.doc_id:
                raise AnnotationValidationError("标注结果不属于当前古籍。")
        source_context = passage_payload.get("context")
        if source_context is not None and source_context != passage.context:
            raise AnnotationConflictError("标注结果对应的正文与当前古籍不一致。")
        if passage_payload.get("context_sha256") not in (None, context_sha256(passage.context)):
            raise AnnotationConflictError("标注结果对应的正文摘要与当前古籍不一致。")

    label_payload = document.get("label")
    if label_payload is None and "schema_version" in document:
        label_payload = document
    if not isinstance(label_payload, dict):
        raise AnnotationValidationError("标注结果必须包含 label 对象。")

    try:
        label = ExtractionAnnotationLabel.model_validate(label_payload)
    except ValidationError as exc:
        details = [
            {
                "path": ".".join(str(part) for part in issue["loc"]),
                "message": issue["msg"],
                "code": "invalid_import_label",
            }
            for issue in exc.errors()
        ]
        raise AnnotationValidationError(details) from exc

    if allow_validation_issues:
        discard_out_of_range_evidence(label, passage.context)
        issues: list[dict[str, str]] = []
    else:
        issues = (
            validate_for_submission(label, passage.context, task.spec_version, db)
            if require_submission
            else validate_evidence_offsets(label, passage.context)
        )
        if label.schema_version != task.spec_version:
            issues.append(
                _issue("schema_version", "标签规范版本与任务版本不一致。", "schema_version_mismatch")
            )
    if issues:
        raise AnnotationValidationError(issues)
    return label


class ExtractionAnnotationService:
    def __init__(self, db: Session):
        self.db = db

    def create_task(
        self,
        payload: ExtractionTaskCreateRequest,
        current_user: User,
    ) -> ExtractionTaskSummaryResponse:
        passage = self.db.get(Passage, payload.passage_id)
        if passage is None:
            raise AnnotationNotFoundError("未找到指定古籍。")
        digest = context_sha256(passage.context)
        existing = (
            self.db.query(ExtractionAnnotationTask)
            .filter(
                ExtractionAnnotationTask.passage_id == passage.doc_id,
                ExtractionAnnotationTask.context_sha256 == digest,
                ExtractionAnnotationTask.spec_version == payload.spec_version,
            )
            .first()
        )
        if existing is not None:
            return self._build_summary(existing, passage, current_user)

        task = ExtractionAnnotationTask(
            passage_id=passage.doc_id,
            context_sha256=digest,
            spec_version=payload.spec_version,
            status="open",
            required_annotation_count=payload.required_annotation_count,
            priority=payload.priority,
            created_by=current_user.id,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return self._build_summary(task, passage, current_user)

    def list_tasks(self, current_user: User) -> list[ExtractionTaskSummaryResponse]:
        tasks = (
            self.db.query(ExtractionAnnotationTask)
            .filter(ExtractionAnnotationTask.status != "archived")
            .order_by(
                ExtractionAnnotationTask.priority.desc(),
                ExtractionAnnotationTask.updated_at.desc(),
                ExtractionAnnotationTask.id.asc(),
            )
            .all()
        )
        if not tasks:
            return []
        passage_ids = {task.passage_id for task in tasks}
        passages = {
            passage.doc_id: passage
            for passage in self.db.query(Passage).filter(Passage.doc_id.in_(passage_ids)).all()
        }
        return [
            self._build_summary(task, passages[task.passage_id], current_user)
            for task in tasks
            if task.passage_id in passages
        ]

    def claim_task(
        self,
        task_id: int,
        current_user: User,
    ) -> ExtractionTaskDetailResponse:
        task, passage = self._get_task_and_passage(task_id)
        existing = self._get_own_submission(task_id, current_user.id)
        if existing is not None:
            return self._build_detail(task, passage, existing)
        if task.status in {"ready_for_adjudication", "completed", "archived"}:
            raise AnnotationConflictError("该任务已停止领取。")

        used_slots = {
            row[0]
            for row in self.db.query(ExtractionAnnotationSubmission.slot_no)
            .filter(ExtractionAnnotationSubmission.task_id == task_id)
            .all()
        }
        slot_no = next(
            (slot for slot in range(1, task.required_annotation_count + 1) if slot not in used_slots),
            None,
        )
        if slot_no is None:
            raise AnnotationConflictError("该任务的盲标槽位已满。")

        label = ExtractionAnnotationLabel(schema_version=task.spec_version)
        submission = ExtractionAnnotationSubmission(
            task_id=task.id,
            annotator_id=current_user.id,
            slot_no=slot_no,
            state="draft",
            label_json=_dump_label(label),
            revision=0,
        )
        task.status = "in_progress"
        self.db.add_all([task, submission])
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AnnotationConflictError("任务刚刚被其他标注员领取，请刷新任务队列。") from exc
        self.db.refresh(task)
        self.db.refresh(submission)
        return self._build_detail(task, passage, submission)

    def release_draft(
        self,
        task_id: int,
        submission_id: int,
        current_user: User,
    ) -> ExtractionTaskSummaryResponse:
        if current_user.role != "admin":
            raise AnnotationForbiddenError("仅管理员可以释放标注槽位。")

        task, passage = self._get_task_and_passage(task_id)
        submission = self.db.get(ExtractionAnnotationSubmission, submission_id)
        if submission is None or submission.task_id != task.id:
            raise AnnotationNotFoundError("未找到指定的标注槽位。")
        if submission.state == "submitted" or submission.submitted_at is not None:
            raise AnnotationConflictError("已提交的盲标结果不能释放。")
        if submission.state != "draft":
            raise AnnotationConflictError("只有未提交的草稿槽位可以释放。")

        self.db.delete(submission)
        self.db.flush()
        self._refresh_task_status(task)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return self._build_summary(task, passage, current_user)

    def reset_task(
        self,
        task_id: int,
        current_user: User,
    ) -> ExtractionTaskSummaryResponse:
        """管理员清空任务全部标注数据，并保留任务配置重新开放。"""

        self._assert_admin(current_user, "仅管理员可以重置标注任务。")
        task, passage = self._get_task_and_passage(task_id)
        self._clear_task_data(task.id)
        task.status = "open"
        task.updated_at = datetime.now(timezone.utc)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return self._build_summary(task, passage, current_user)

    def delete_task(self, task_id: int, current_user: User) -> None:
        """管理员删除任务及其全部标注/裁定数据，但不删除对应古籍。"""

        self._assert_admin(current_user, "仅管理员可以删除标注任务。")
        task, _ = self._get_task_and_passage(task_id)
        self._clear_task_data(task.id)
        self.db.delete(task)
        self.db.commit()

    def get_task_detail(
        self,
        task_id: int,
        current_user: User,
    ) -> ExtractionTaskDetailResponse:
        task, passage = self._get_task_and_passage(task_id)
        submission = self._get_own_submission(task_id, current_user.id)
        if submission is None:
            raise AnnotationForbiddenError("请先领取该任务。")
        return self._build_detail(task, passage, submission)

    def save_draft(
        self,
        task_id: int,
        current_user: User,
        revision: int,
        label: ExtractionAnnotationLabel,
    ) -> ExtractionTaskDetailResponse:
        task, passage = self._get_task_and_passage(task_id)
        submission = self._require_editable_submission(task_id, current_user.id)
        self._assert_revision(submission, revision)
        self._assert_current_context(task, passage)
        issues = validate_evidence_offsets(label, passage.context)
        if label.schema_version != task.spec_version:
            issues.append(
                _issue("schema_version", "标签规范版本与任务版本不一致。", "schema_version_mismatch")
            )
        if issues:
            raise AnnotationValidationError(issues)

        submission.label_json = _dump_label(label)
        submission.revision += 1
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        return self._build_detail(task, passage, submission)

    def import_draft(
        self,
        task_id: int,
        current_user: User,
        revision: int,
        content: bytes | str,
        *,
        allow_validation_issues: bool = False,
        source_ai_job_id: int | None = None,
    ) -> ExtractionTaskDetailResponse:
        """把外部 YAML/JSON 标注结果导入当前用户的独立盲标草稿。"""

        task, passage = self._get_task_and_passage(task_id)
        submission = self._require_editable_submission(task_id, current_user.id)
        self._assert_revision(submission, revision)
        self._assert_current_context(task, passage)
        label = parse_imported_label(
            content,
            task,
            passage,
            self.db,
            require_submission=False,
            allow_validation_issues=allow_validation_issues,
        )

        submission.label_json = _dump_label(label)
        if source_ai_job_id is not None:
            source_job = self.db.get(ExtractionAIAnnotationJob, source_ai_job_id)
            if (
                source_job is None
                or source_job.task_id != task_id
                or source_job.requested_by != current_user.id
            ):
                raise AnnotationNotFoundError("未找到可关联的 AI 标注任务。")
            if source_job.status != "success":
                raise AnnotationConflictError("只有已完成的 AI 标注结果才能关联到盲标草稿。")
            submission.source_ai_job_id = source_job.id
            submission.ai_imported_at = datetime.now(timezone.utc)
        else:
            # 人工导入新文件代表来源发生变化，不能继续把结果归因给旧 AI 任务。
            submission.source_ai_job_id = None
            submission.ai_imported_at = None
        submission.revision += 1
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        return self._build_detail(task, passage, submission)

    def submit(
        self,
        task_id: int,
        current_user: User,
        revision: int,
        label: ExtractionAnnotationLabel,
    ) -> ExtractionTaskDetailResponse:
        task, passage = self._get_task_and_passage(task_id)
        submission = self._require_editable_submission(task_id, current_user.id)
        self._assert_revision(submission, revision)
        self._assert_current_context(task, passage)
        issues = validate_for_submission(label, passage.context, task.spec_version, self.db)
        if issues:
            raise AnnotationValidationError(issues)

        submission.label_json = _dump_label(label)
        submission.state = "submitted"
        submission.submitted_at = datetime.now(timezone.utc)
        submission.revision += 1
        self.db.add(submission)
        self._record_ai_submission_metrics(submission, label)
        self.db.flush()
        self._refresh_task_status(task)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        self.db.refresh(submission)
        return self._build_detail(task, passage, submission)

    def _record_ai_submission_metrics(
        self,
        submission: ExtractionAnnotationSubmission,
        final_label: ExtractionAnnotationLabel,
    ) -> None:
        """封口 AI 标注会话的总耗时与首轮到最终提交差异。

        统计失败不能阻断人工标注提交，因此所有兼容旧数据的异常都只写日志。
        """

        if submission.source_ai_job_id is None or submission.submitted_at is None:
            return
        try:
            source_job = self.db.get(
                ExtractionAIAnnotationJob,
                submission.source_ai_job_id,
            )
            if source_job is None:
                return
            root_job = self._find_ai_generation_root(source_job)
            if root_job.operation != "generate":
                return

            result_payload = json.loads(root_job.result_json or "{}")
            first_label_payload = result_payload.get("label")
            if not isinstance(first_label_payload, dict):
                document = result_payload.get("document")
                first_label_payload = (
                    document.get("label") if isinstance(document, dict) else None
                )

            category_counts: dict[str, int] = {}
            difference_count = 0
            if isinstance(first_label_payload, dict):
                first_label = ExtractionAnnotationLabel.model_validate(first_label_payload)
                # 延迟导入以避免标注服务与裁定服务的模块初始化互相依赖。
                from app.services.extraction_adjudication_service import (
                    build_adjudication_differences,
                )

                differences = build_adjudication_differences(first_label, final_label)
                difference_count = len(differences)
                for difference in differences:
                    category_counts[difference.category] = (
                        category_counts.get(difference.category, 0) + 1
                    )

            root_job.submitted_at = submission.submitted_at
            root_job.final_difference_count = difference_count
            root_job.final_differences_json = json.dumps(
                category_counts,
                ensure_ascii=False,
            )
            self.db.add(root_job)
        except Exception:
            logger.exception(
                "Failed to finalize AI annotation metrics submission_id=%s source_job_id=%s",
                submission.id,
                submission.source_ai_job_id,
            )

    def _find_ai_generation_root(
        self,
        job: ExtractionAIAnnotationJob,
    ) -> ExtractionAIAnnotationJob:
        current = job
        visited = {current.id}
        while current.operation == "repair" and current.source_job_id is not None:
            parent = self.db.get(ExtractionAIAnnotationJob, current.source_job_id)
            if parent is None or parent.id in visited:
                break
            visited.add(parent.id)
            current = parent
        return current

    def _get_task_and_passage(self, task_id: int) -> tuple[ExtractionAnnotationTask, Passage]:
        task = self.db.get(ExtractionAnnotationTask, task_id)
        if task is None:
            raise AnnotationNotFoundError("未找到标注任务。")
        passage = self.db.get(Passage, task.passage_id)
        if passage is None:
            raise AnnotationNotFoundError("标注任务对应的古籍不存在。")
        return task, passage

    @staticmethod
    def _assert_admin(current_user: User, message: str) -> None:
        if current_user.role != "admin":
            raise AnnotationForbiddenError(message)

    def _clear_task_data(self, task_id: int) -> None:
        """显式删除任务子表，兼容 SQLite 未开启 foreign_keys 的部署/测试环境。"""

        self.db.query(ExtractionAdjudicationDraft).filter(
            ExtractionAdjudicationDraft.task_id == task_id
        ).delete(synchronize_session=False)
        self.db.query(ExtractionGoldVersion).filter(
            ExtractionGoldVersion.task_id == task_id
        ).delete(synchronize_session=False)
        self.db.query(ExtractionAnnotationSubmission).filter(
            ExtractionAnnotationSubmission.task_id == task_id
        ).delete(synchronize_session=False)

    def _get_own_submission(
        self,
        task_id: int,
        user_id: int,
    ) -> ExtractionAnnotationSubmission | None:
        return (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(
                ExtractionAnnotationSubmission.task_id == task_id,
                ExtractionAnnotationSubmission.annotator_id == user_id,
            )
            .first()
        )

    def _require_editable_submission(
        self,
        task_id: int,
        user_id: int,
    ) -> ExtractionAnnotationSubmission:
        submission = self._get_own_submission(task_id, user_id)
        if submission is None:
            raise AnnotationForbiddenError("请先领取该任务。")
        if submission.state == "submitted":
            raise AnnotationConflictError("该盲标结果已经提交并锁定。")
        return submission

    @staticmethod
    def _assert_revision(submission: ExtractionAnnotationSubmission, revision: int) -> None:
        if submission.revision != revision:
            raise AnnotationConflictError(
                f"草稿版本冲突：服务端 revision={submission.revision}，请刷新后再编辑。"
            )

    @staticmethod
    def _assert_current_context(task: ExtractionAnnotationTask, passage: Passage) -> None:
        if task.context_sha256 != context_sha256(passage.context):
            raise AnnotationConflictError("古籍正文已发生变化，该任务不能继续提交。")

    def _refresh_task_status(self, task: ExtractionAnnotationTask) -> None:
        submissions = (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(ExtractionAnnotationSubmission.task_id == task.id)
            .all()
        )
        submitted_count = sum(item.state == "submitted" for item in submissions)
        if submitted_count >= task.required_annotation_count:
            task.status = "ready_for_adjudication"
        elif submissions:
            task.status = "in_progress"
        else:
            task.status = "open"

    def _build_summary(
        self,
        task: ExtractionAnnotationTask,
        passage: Passage,
        current_user: User,
    ) -> ExtractionTaskSummaryResponse:
        submissions = (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(ExtractionAnnotationSubmission.task_id == task.id)
            .all()
        )
        own = next((item for item in submissions if item.annotator_id == current_user.id), None)
        submitted_count = sum(item.state == "submitted" for item in submissions)
        assignments: list[ExtractionTaskAssignmentResponse] = []
        if current_user.role == "admin" and submissions:
            annotator_ids = {item.annotator_id for item in submissions}
            annotators = {
                user.id: user
                for user in self.db.query(User).filter(User.id.in_(annotator_ids)).all()
            }
            assignments = [
                ExtractionTaskAssignmentResponse(
                    submission_id=item.id,
                    slot_no=item.slot_no,
                    annotator_id=item.annotator_id,
                    annotator_email=(
                        annotators[item.annotator_id].email
                        if item.annotator_id in annotators
                        else f"用户 #{item.annotator_id}"
                    ),
                    state=item.state,
                    submitted_at=item.submitted_at,
                    updated_at=item.updated_at,
                )
                for item in sorted(submissions, key=lambda submission: submission.slot_no)
            ]
        return ExtractionTaskSummaryResponse(
            id=task.id,
            passage_id=task.passage_id,
            passage_title=passage.title,
            context_sha256=task.context_sha256,
            spec_version=task.spec_version,
            status=task.status,
            priority=task.priority,
            required_annotation_count=task.required_annotation_count,
            claimed_count=len(submissions),
            submitted_count=submitted_count,
            available_slots=max(task.required_annotation_count - len(submissions), 0),
            submission_id=own.id if own else None,
            submission_state=own.state if own else None,
            slot_no=own.slot_no if own else None,
            revision=own.revision if own else None,
            updated_at=task.updated_at,
            assignments=assignments,
        )

    @staticmethod
    def _build_detail(
        task: ExtractionAnnotationTask,
        passage: Passage,
        submission: ExtractionAnnotationSubmission,
    ) -> ExtractionTaskDetailResponse:
        return ExtractionTaskDetailResponse(
            id=task.id,
            context_sha256=task.context_sha256,
            spec_version=task.spec_version,
            status=task.status,
            required_annotation_count=task.required_annotation_count,
            passage=AnnotationPassageResponse(
                doc_id=passage.doc_id,
                title=passage.title,
                context=passage.context,
                source_type=passage.source_type,
                workflow_status=passage.workflow_status,
            ),
            submission=ExtractionSubmissionResponse(
                id=submission.id,
                task_id=submission.task_id,
                slot_no=submission.slot_no,
                state=submission.state,
                revision=submission.revision,
                label=_load_label(submission),
                submitted_at=submission.submitted_at,
                updated_at=submission.updated_at,
            ),
        )
