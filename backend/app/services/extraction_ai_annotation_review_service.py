"""首轮 AI 标注的固定规范人工语义质检。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Iterable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationResultResponse,
    AIAnnotationReviewDecision,
    AIAnnotationReviewItem,
    AIAnnotationReviewOmission,
    AIAnnotationReviewResponse,
    AIAnnotationReviewStandard,
    AIAnnotationReviewSummary,
    AIAnnotationReviewUpdateRequest,
    EvidenceSpan,
    ExtractionAnnotationLabel,
)
from app.services.extraction_annotation_service import (
    ExtractionAnnotationServiceError,
    codepoint_to_utf16_offset,
    evidence_granularity_issue,
)


AI_ANNOTATION_REVIEW_STANDARD_VERSION = "2026-09-07-v1"


AI_ANNOTATION_REVIEW_STANDARDS = [
    AIAnnotationReviewStandard(
        id="S01",
        group="材料",
        title="材料与来源",
        instruction="只依据当前标题、完整正文和给定字典；体裁、材料状态和文献属性均需有直接依据。",
    ),
    AIAnnotationReviewStandard(
        id="S02",
        group="人物",
        title="人物覆盖",
        instruction="全文可唯一识别的人物都应建立；泛称、群体、虚指和名称组成部分不得误建。",
    ),
    AIAnnotationReviewStandard(
        id="S03",
        group="人物",
        title="人物合并",
        instruction="同一人的明确别名应合并，不同人物不得因同姓、同官职或外部常识被合并。",
    ),
    AIAnnotationReviewStandard(
        id="S04",
        group="人物",
        title="人物属性",
        instruction="姓名补全、字、号、封号、谥号和性别只采用原文明示内容，不使用外部知识补齐。",
    ),
    AIAnnotationReviewStandard(
        id="S05",
        group="人物",
        title="人物分级",
        instruction="普通篇目恰有一个一级人物；二级人物具有强关联和具体作用；三级人物仅保留最小信息。",
    ),
    AIAnnotationReviewStandard(
        id="S06",
        group="证据",
        title="实体级人物证据",
        instruction="人物 mention 只覆盖姓名、称谓或稳定指代本身，位置可回放，不选择完整句子。",
    ),
    AIAnnotationReviewStandard(
        id="S07",
        group="事件",
        title="五类事件覆盖",
        instruction="一级、二级人物逐项检查出生、籍贯、死亡、埋葬、任职，不能静默漏检。",
    ),
    AIAnnotationReviewStandard(
        id="S08",
        group="事件",
        title="事件归属",
        instruction="每条事件确属当前人物，不能把上下文中其他人物的事实错误挂接。",
    ),
    AIAnnotationReviewStandard(
        id="S09",
        group="事件",
        title="事件字段",
        instruction="时间、地点、官职和事实状态不靠推断；籍贯不填时间，只有任职填写官职。",
    ),
    AIAnnotationReviewStandard(
        id="S10",
        group="证据",
        title="最小事实证据",
        instruction="事件证据选择最小充分事实短语；跨句事实使用多条短证据，不用整句或整段包办。",
    ),
    AIAnnotationReviewStandard(
        id="S11",
        group="历史事件",
        title="历史事件",
        instruction="正文须明确出现具体事件；名称、人物作用、字典内外状态和证据应相互一致。",
    ),
    AIAnnotationReviewStandard(
        id="S12",
        group="关系",
        title="人物关系",
        instruction="关系两端、方向、正反代码和说明正确，不从外部知识或单纯共现推导。",
    ),
    AIAnnotationReviewStandard(
        id="S13",
        group="证据",
        title="关系证据",
        instruction="证据同时支持关系两端和关系触发语，使用最小充分短语而非完整句段。",
    ),
    AIAnnotationReviewStandard(
        id="S14",
        group="异常",
        title="排除项",
        instruction="泛称、群体、虚指、名称组成部分等应记录排除原因和原文位置。",
    ),
    AIAnnotationReviewStandard(
        id="S15",
        group="异常",
        title="不确定项",
        instruction="残缺、归属不明、同名未决等内容不得被强行写成确定事实。",
    ),
    AIAnnotationReviewStandard(
        id="S16",
        group="异常",
        title="工具限制",
        instruction="原文事实不确定与事实明确但 Schema/证据来源不支持必须严格区分。",
    ),
    AIAnnotationReviewStandard(
        id="S17",
        group="证据",
        title="证据一致性",
        instruction="每条事实都有正文证据，文字与 Unicode、UTF-16 两套偏移均能准确回放。",
    ),
    AIAnnotationReviewStandard(
        id="S18",
        group="完整性",
        title="全文完整性",
        instruction="再次通读全文，确认人物、五类事件、历史事件、人物关系和重要异常均无缺漏。",
    ),
]


class ExtractionAIAnnotationReviewError(ExtractionAnnotationServiceError):
    """首轮 AI 质检的可预期业务错误。"""

    status_code = 400

    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_list(raw: str) -> list[dict]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _nonempty(parts: Iterable[tuple[str, object]]) -> str:
    values: list[str] = []
    for label, value in parts:
        if value is None or value == "" or value == []:
            continue
        values.append(f"{label}：{value}")
    return "；".join(values)


def _evidence_preview(evidence: list[EvidenceSpan]) -> str:
    if not evidence:
        return "无正文证据"
    return " / ".join(f"“{item.quote}”" for item in evidence[:3])


def build_ai_annotation_review_items(
    label: ExtractionAnnotationLabel,
) -> list[AIAnnotationReviewItem]:
    """由首轮标签确定性生成检查项，不向 LLM 请求自由检查计划。"""

    items: list[AIAnnotationReviewItem] = []

    def add(
        item_id: str,
        category: str,
        path: str,
        title: str,
        question: str,
        standard_ids: list[str],
        *,
        detail: str = "",
        evidence: list[EvidenceSpan] | None = None,
    ) -> None:
        items.append(
            AIAnnotationReviewItem(
                id=item_id,
                category=category,
                path=path,
                title=title,
                question=question,
                detail=detail,
                standard_ids=standard_ids,
                evidence=evidence or [],
            )
        )

    passage = label.passage
    add(
        "passage:attributes",
        "材料",
        "passage",
        "文献属性与材料状态",
        "AI 对材料体裁、完整性、性别、宗教身份、字与年号的判断是否都有原文依据？",
        ["S01", "S15", "S16"],
        detail=_nonempty(
            (
                ("来源", passage.source_type),
                ("材料", passage.material_status),
                ("女性", passage.is_female),
                ("残损", passage.is_damaged),
                ("宗教人物", passage.is_clergy),
                ("有字", passage.has_courtesy_name),
                ("年号", passage.era),
                ("依据", passage.era_basis),
                ("说明", passage.note),
            )
        ),
    )
    for item_id, category, title, question, standards in (
        ("coverage:persons", "人物", "全文人物覆盖", "通读全文后，AI 是否没有漏建或误建可唯一识别的人物？", ["S02", "S03", "S18"]),
        ("coverage:events", "事件", "全文五类事件覆盖", "通读全文后，一级、二级人物的出生、籍贯、死亡、埋葬、任职是否均无漏标？", ["S07", "S18"]),
        ("coverage:history", "历史事件", "全文历史事件覆盖", "正文明确出现且与人物有关的具体历史事件是否均已标注？", ["S11", "S18"]),
        ("coverage:relations", "关系", "全文人物关系覆盖", "正文明确的人物关系是否均已标注，且没有把单纯共现当关系？", ["S12", "S18"]),
        ("coverage:exceptions", "异常", "全文异常与限制覆盖", "应排除、暂不能判断或工具暂不支持的内容是否均已如实记录？", ["S14", "S15", "S16", "S18"]),
    ):
        add(item_id, category, item_id.replace(":", "."), title, question, standards)

    person_names = {person.key: person.name_surface or person.key for person in label.persons}
    for person_index, person in enumerate(label.persons):
        person_path = f"persons.{person_index}"
        person_name = person.name_surface or person.key
        person_evidence = list(person.mentions)
        add(
            f"person:{person.key}:identity",
            "人物",
            person_path,
            f"人物 · {person_name}",
            f"人物“{person_name}”的建立、别名合并及姓名属性是否正确？",
            ["S02", "S03", "S04"],
            detail=_nonempty(
                (
                    ("补全名", person.completed_name),
                    ("补全依据", person.completion_reason),
                    ("字", person.courtesy_name),
                    ("号", person.hao),
                    ("称号", "、".join(person.titles)),
                )
            ),
            evidence=person_evidence,
        )
        add(
            f"person:{person.key}:level",
            "人物",
            f"{person_path}.level",
            f"人物分级 · {person_name}",
            f"将“{person_name}”判为 {person.level} 级是否符合叙事中心、强关联和具体作用标准？",
            ["S05"],
            detail=person.level_reason,
            evidence=person_evidence,
        )
        for mention_index, evidence in enumerate(person.mentions):
            add(
                f"person:{person.key}:mention:{mention_index}",
                "证据",
                f"{person_path}.mentions.{mention_index}",
                f"人物证据 · {person_name}",
                f"“{evidence.quote}”是否只覆盖该人物的实体或稳定指代，且位置正确？",
                ["S06", "S17"],
                detail=f"字符位置 {evidence.start}—{evidence.end}",
                evidence=[evidence],
            )
        if person.level in (1, 2):
            checks = "；".join(
                f"{event_type}={state}" for event_type, state in person.event_checks.items()
            )
            add(
                f"person:{person.key}:event-coverage",
                "事件",
                f"{person_path}.event_checks",
                f"五类检查 · {person_name}",
                f"“{person_name}”的五类事件状态是否逐类检查、与正文及已建事件一致？",
                ["S07", "S08"],
                detail=checks,
                evidence=person_evidence,
            )

        for event_index, event in enumerate(person.life_events):
            time_detail = _nonempty(
                (
                    ("状态", event.time.state),
                    ("原文", event.time.raw),
                    ("年号", event.time.era),
                    ("年次", event.time.era_year),
                    ("公元", event.time.gregorian_year),
                    ("月", event.time.month_text),
                    ("日", event.time.day_text),
                )
            )
            location_detail = _nonempty(
                (
                    ("状态", event.location.state),
                    ("原文", event.location.raw),
                    ("道", event.location.dao),
                    ("府", event.location.fu),
                    ("州", event.location.zhou),
                    ("郡", event.location.jun),
                    ("县", event.location.xian),
                    ("其他", event.location.other),
                )
            )
            add(
                f"event:{person.key}:{event.key}:{event_index}",
                "事件",
                f"{person_path}.life_events.{event_index}",
                f"{event.event_type} · {person_name}",
                f"“{person_name}”的{event.event_type}事实、归属、状态及字段填写是否正确？",
                ["S08", "S09", "S10", "S17"],
                detail=_nonempty(
                    (
                        ("事实状态", event.state),
                        ("时间", time_detail),
                        ("地点", location_detail),
                        ("官职", event.official_title),
                        ("说明", event.note),
                        ("证据", _evidence_preview(list(event.evidence))),
                    )
                ),
                evidence=list(event.evidence),
            )

        for event_index, event in enumerate(person.historical_events):
            add(
                f"history:{person.key}:{event.key}:{event_index}",
                "历史事件",
                f"{person_path}.historical_events.{event_index}",
                f"历史事件 · {event.event_name or '未命名'}",
                f"“{person_name}”与历史事件“{event.event_name or '未命名'}”的关联是否明确且填写正确？",
                ["S11", "S10", "S17"],
                detail=_nonempty(
                    (
                        ("作用", event.relation_summary),
                        ("表外", event.outside_dictionary),
                        ("状态", event.state),
                        ("说明", event.note),
                        ("证据", _evidence_preview(list(event.evidence))),
                    )
                ),
                evidence=list(event.evidence),
            )

    for relation_index, relation in enumerate(label.person_relations):
        source = person_names.get(relation.source_person_key, relation.source_person_key)
        target = person_names.get(relation.target_person_key, relation.target_person_key)
        add(
            f"relation:{relation.key}:{relation_index}",
            "关系",
            f"person_relations.{relation_index}",
            f"人物关系 · {source} → {target}",
            f"“{source}”指向“{target}”的关系、方向、正反代码和事实状态是否正确？",
            ["S12", "S13", "S17"],
            detail=_nonempty(
                (
                    ("正向", "".join(relation.codes)),
                    ("反向", "".join(relation.reverse_codes)),
                    ("正向说明", relation.note),
                    ("反向说明", relation.reverse_note),
                    ("状态", relation.state),
                    ("证据", _evidence_preview(list(relation.evidence))),
                )
            ),
            evidence=list(relation.evidence),
        )

    auxiliary = (
        ("excluded_mentions", "排除项", label.excluded_mentions, ["S14", "S06", "S17"]),
        ("unresolved_items", "待判断", label.unresolved_items, ["S15", "S17"]),
        ("schema_conflicts", "工具限制", label.schema_conflicts, ["S16", "S17"]),
    )
    for field, category, values, standards in auxiliary:
        for item_index, value in enumerate(values):
            evidence = list(value.evidence)
            detail = _nonempty(
                (
                    ("原因", getattr(value, "reason", "")),
                    ("分类", getattr(value, "category", "")),
                    ("代码", getattr(value, "code", "")),
                    ("说明", getattr(value, "note", "")),
                    ("证据", _evidence_preview(evidence)),
                )
            )
            add(
                f"aux:{field}:{value.key}:{item_index}",
                "异常",
                f"{field}.{item_index}",
                f"{category} · {value.key}",
                f"该{category}的分类、说明和证据是否正确且必要？",
                standards,
                detail=detail,
                evidence=evidence,
            )
    return items


class ExtractionAIAnnotationReviewService:
    """读取首轮结果并管理其人工质检草稿和锁定结果。"""

    def __init__(self, db: Session):
        self.db = db

    def get_review(self, job_id: int, current_user: User) -> AIAnnotationReviewResponse:
        job, result, passage = self._review_context(job_id, current_user)
        row = self._get_row(job.id)
        return self._to_response(job, result, passage, current_user.id, row)

    def save_review(
        self,
        job_id: int,
        payload: AIAnnotationReviewUpdateRequest,
        current_user: User,
    ) -> AIAnnotationReviewResponse:
        job, result, passage = self._review_context(job_id, current_user)
        row = self._get_row(job.id)
        if row is not None and row.status == "submitted":
            raise ExtractionAIAnnotationReviewError("首轮质检已提交，不能继续修改", status_code=409)
        items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
        decisions, omissions = self._normalize_update(payload, items, passage)
        summary = self._summary(decisions, omissions)
        row = self._write_row(job, current_user.id, row, decisions, omissions, summary)
        return self._to_response(job, result, passage, current_user.id, row)

    def submit_review(
        self,
        job_id: int,
        payload: AIAnnotationReviewUpdateRequest,
        current_user: User,
    ) -> AIAnnotationReviewResponse:
        job, result, passage = self._review_context(job_id, current_user)
        row = self._get_row(job.id)
        if row is not None and row.status == "submitted":
            return self._to_response(job, result, passage, current_user.id, row)
        items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
        decisions, omissions = self._normalize_update(payload, items, passage)
        self._validate_submission(decisions, omissions)
        summary = self._summary(decisions, omissions)
        row = self._write_row(job, current_user.id, row, decisions, omissions, summary)
        row.status = "submitted"
        row.submitted_at = _now()
        row.updated_at = _now()
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return self._to_response(job, result, passage, current_user.id, row)

    def _review_context(
        self,
        job_id: int,
        current_user: User,
    ) -> tuple[ExtractionAIAnnotationJob, AIAnnotationResultResponse, Passage]:
        requested = self.db.get(ExtractionAIAnnotationJob, job_id)
        if requested is None or requested.requested_by != current_user.id:
            raise ExtractionAIAnnotationReviewError("未找到指定的 AI 标注任务", status_code=404)
        job = self._generation_job(requested)
        if job is None or job.requested_by != current_user.id:
            raise ExtractionAIAnnotationReviewError("当前任务没有可追溯的首轮 AI 生成结果", status_code=409)
        if job.status != "success" or not job.result_json:
            raise ExtractionAIAnnotationReviewError("首轮 AI 生成尚未成功完成，暂不能质检", status_code=409)
        try:
            result = AIAnnotationResultResponse.model_validate(json.loads(job.result_json))
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionAIAnnotationReviewError("首轮 AI 结果无法读取", status_code=409) from exc
        if result.document is None:
            raise ExtractionAIAnnotationReviewError("首轮 AI 结果无法解析为标注对象，需重新生成", status_code=409)
        passage = self.db.get(Passage, job.passage_id)
        if passage is None:
            raise ExtractionAIAnnotationReviewError("首轮 AI 结果对应的古籍不存在", status_code=404)
        return job, result, passage

    def _generation_job(
        self,
        job: ExtractionAIAnnotationJob,
    ) -> ExtractionAIAnnotationJob | None:
        visited: set[int] = set()
        current: ExtractionAIAnnotationJob | None = job
        while current is not None and current.operation != "generate":
            if current.id in visited or current.source_job_id is None:
                return None
            visited.add(current.id)
            current = self.db.get(ExtractionAIAnnotationJob, current.source_job_id)
        return current

    def _get_row(self, generation_job_id: int) -> ExtractionAIAnnotationReview | None:
        return (
            self.db.query(ExtractionAIAnnotationReview)
            .filter(ExtractionAIAnnotationReview.generation_job_id == generation_job_id)
            .first()
        )

    def _normalize_update(
        self,
        payload: AIAnnotationReviewUpdateRequest,
        items: list[AIAnnotationReviewItem],
        passage: Passage,
    ) -> tuple[list[AIAnnotationReviewDecision], list[AIAnnotationReviewOmission]]:
        valid_ids = {item.id for item in items}
        seen: set[str] = set()
        decision_by_id: dict[str, AIAnnotationReviewDecision] = {}
        for decision in payload.decisions:
            if decision.item_id in seen:
                raise ExtractionAIAnnotationReviewError("质检项不能重复提交", status_code=422)
            if decision.item_id not in valid_ids:
                raise ExtractionAIAnnotationReviewError("质检项与当前首轮结果不一致，请刷新页面", status_code=409)
            seen.add(decision.item_id)
            decision_by_id[decision.item_id] = AIAnnotationReviewDecision(
                item_id=decision.item_id,
                verdict=decision.verdict,
                reason=decision.reason.strip(),
            )
        decisions = [
            decision_by_id.get(item.id, AIAnnotationReviewDecision(item_id=item.id))
            for item in items
        ]

        omission_ids: set[str] = set()
        omissions: list[AIAnnotationReviewOmission] = []
        for omission in payload.omissions:
            if omission.id in omission_ids:
                raise ExtractionAIAnnotationReviewError("缺漏记录 ID 不能重复", status_code=422)
            omission_ids.add(omission.id)
            normalized = AIAnnotationReviewOmission(
                id=omission.id,
                category=omission.category.strip() or "其他",
                target_path=omission.target_path.strip(),
                description=omission.description.strip(),
                correction=omission.correction.strip(),
                evidence=omission.evidence,
                evidence_note=omission.evidence_note.strip(),
            )
            self._validate_omission_evidence(normalized, passage.context)
            omissions.append(normalized)
        return decisions, omissions

    @staticmethod
    def _validate_omission_evidence(omission: AIAnnotationReviewOmission, context: str) -> None:
        for evidence in omission.evidence:
            if evidence.end > len(context) or context[evidence.start : evidence.end] != evidence.quote:
                raise ExtractionAIAnnotationReviewError("缺漏记录的证据与当前正文位置不一致", status_code=422)
            if (
                evidence.start_utf16 != codepoint_to_utf16_offset(context, evidence.start)
                or evidence.end_utf16 != codepoint_to_utf16_offset(context, evidence.end)
            ):
                raise ExtractionAIAnnotationReviewError("缺漏记录的 UTF-16 证据位置不一致", status_code=422)
            granularity_issue = evidence_granularity_issue(evidence, "fact")
            if granularity_issue is not None:
                raise ExtractionAIAnnotationReviewError(
                    f"缺漏记录的原文证据粒度过大：{granularity_issue[1]}",
                    status_code=422,
                )

    @staticmethod
    def _validate_submission(
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
    ) -> None:
        unreviewed = [item for item in decisions if item.verdict == "unreviewed"]
        if unreviewed:
            raise ExtractionAIAnnotationReviewError(
                f"还有 {len(unreviewed)} 个检查项未判断，不能提交质检",
                status_code=422,
            )
        missing_reasons = [
            item for item in decisions if item.verdict == "incorrect" and not item.reason
        ]
        if missing_reasons:
            raise ExtractionAIAnnotationReviewError(
                f"有 {len(missing_reasons)} 个错误项尚未填写理由",
                status_code=422,
            )
        for index, omission in enumerate(omissions, start=1):
            if not omission.target_path or not omission.description or not omission.correction:
                raise ExtractionAIAnnotationReviewError(
                    f"第 {index} 条缺漏必须填写目标字段、缺少内容和补录内容",
                    status_code=422,
                )
            if not omission.evidence and not omission.evidence_note:
                raise ExtractionAIAnnotationReviewError(
                    f"第 {index} 条缺漏必须划选原文证据；无法划选时填写证据说明",
                    status_code=422,
                )

    @staticmethod
    def _summary(
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
    ) -> AIAnnotationReviewSummary:
        correct = sum(item.verdict == "correct" for item in decisions)
        incorrect = sum(item.verdict == "incorrect" for item in decisions)
        unreviewed = sum(item.verdict == "unreviewed" for item in decisions)
        denominator = correct + incorrect + len(omissions)
        accuracy = round(correct / denominator, 4) if denominator else 0.0
        return AIAnnotationReviewSummary(
            total_item_count=len(decisions),
            reviewed_item_count=correct + incorrect,
            correct_count=correct,
            incorrect_count=incorrect,
            unreviewed_count=unreviewed,
            omission_count=len(omissions),
            accuracy=accuracy,
        )

    def _write_row(
        self,
        job: ExtractionAIAnnotationJob,
        reviewer_id: int,
        row: ExtractionAIAnnotationReview | None,
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
        summary: AIAnnotationReviewSummary,
    ) -> ExtractionAIAnnotationReview:
        timestamp = _now()
        if row is None:
            row = ExtractionAIAnnotationReview(
                generation_job_id=job.id,
                task_id=job.task_id,
                passage_id=job.passage_id,
                reviewer_id=reviewer_id,
                status="draft",
                standard_version=AI_ANNOTATION_REVIEW_STANDARD_VERSION,
                created_at=timestamp,
                updated_at=timestamp,
            )
        row.decisions_json = json.dumps(
            [item.model_dump(mode="json") for item in decisions],
            ensure_ascii=False,
        )
        row.omissions_json = json.dumps(
            [item.model_dump(mode="json") for item in omissions],
            ensure_ascii=False,
        )
        row.total_item_count = summary.total_item_count
        row.reviewed_item_count = summary.reviewed_item_count
        row.correct_count = summary.correct_count
        row.incorrect_count = summary.incorrect_count
        row.unreviewed_count = summary.unreviewed_count
        row.omission_count = summary.omission_count
        row.accuracy_basis_points = round(summary.accuracy * 10_000)
        row.updated_at = timestamp
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _to_response(
        self,
        job: ExtractionAIAnnotationJob,
        result: AIAnnotationResultResponse,
        passage: Passage,
        reviewer_id: int,
        row: ExtractionAIAnnotationReview | None,
    ) -> AIAnnotationReviewResponse:
        items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
        decisions: list[AIAnnotationReviewDecision] = []
        if row is not None:
            for raw in _json_list(row.decisions_json):
                try:
                    decisions.append(AIAnnotationReviewDecision.model_validate(raw))
                except ValidationError:
                    continue
        decision_by_id = {item.item_id: item for item in decisions}
        for item in items:
            decision = decision_by_id.get(item.id)
            if decision is not None:
                item.verdict = decision.verdict
                item.reason = decision.reason

        omissions: list[AIAnnotationReviewOmission] = []
        if row is not None:
            for raw in _json_list(row.omissions_json):
                try:
                    omissions.append(AIAnnotationReviewOmission.model_validate(raw))
                except ValidationError:
                    continue
        normalized_decisions = [
            AIAnnotationReviewDecision(
                item_id=item.id,
                verdict=item.verdict,
                reason=item.reason,
            )
            for item in items
        ]
        summary = self._summary(normalized_decisions, omissions)
        return AIAnnotationReviewResponse(
            id=row.id if row is not None else None,
            generation_job_id=job.id,
            task_id=job.task_id,
            passage_id=job.passage_id,
            passage_title=passage.title,
            passage_context=passage.context,
            status=(row.status if row is not None else "draft"),  # type: ignore[arg-type]
            standard_version=(
                row.standard_version if row is not None else AI_ANNOTATION_REVIEW_STANDARD_VERSION
            ),
            standards=AI_ANNOTATION_REVIEW_STANDARDS,
            items=items,
            omissions=omissions,
            summary=summary,
            reviewer_id=reviewer_id,
            created_at=row.created_at if row is not None else None,
            updated_at=row.updated_at if row is not None else None,
            submitted_at=row.submitted_at if row is not None else None,
        )
