"""首轮 AI 标注的固定规范人工语义质检。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Literal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.extraction_annotation import ExtractionAnnotationSubmission
from app.models.extraction_ai_annotation import (
    ExtractionAIAnnotationJob,
    ExtractionAIAnnotationReview,
)
from app.models.passage import Passage
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationExportDocument,
    AIAnnotationResultResponse,
    AIAnnotationReviewDecision,
    AIAnnotationReviewItem,
    AIAnnotationReviewOmission,
    AIAnnotationReviewResponse,
    AIAnnotationReviewStandard,
    AIAnnotationReviewSummary,
    AIAnnotationReviewUpdateRequest,
    EVENT_TYPES,
    RELATION_CATEGORIES,
    EvidenceSpan,
    ExtractionAnnotationLabel,
    LifeEventAnnotation,
)
from app.services.extraction_ai_annotation_service import parse_ai_annotation_result
from app.services.extraction_annotation_service import (
    ExtractionAnnotationServiceError,
    codepoint_to_utf16_offset,
    drop_meaningless_excluded_mentions,
    evidence_granularity_issue,
)


AI_ANNOTATION_REVIEW_STANDARD_VERSION = "2026-09-26-v4"


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
        instruction="关系两端、七类类别、10 字内具体表现和证据一致；三级人物的原文明示关系也要保留。",
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


_EMPTY_VALUE = "（空）"
_SOURCE_TYPE_LABELS = {
    "epitaph": "墓志铭或塔铭类",
    "history": "一般历史文献",
    "uncertain": "暂不能判断",
}
_MATERIAL_STATUS_LABELS = {
    "complete": "标题与正文完整匹配",
    "issue": "存在缺页、错配或转换问题",
    "uncertain": "暂不能判断",
}
_CHECK_STATE_LABELS = {
    "unreviewed": "尚未检查",
    "has_fact": "已检查且有事实",
    "not_mentioned": "原文未提及",
    "uncertain": "暂不能判断",
    "unsupported": "工具暂不支持",
}
_FACT_STATE_LABELS = {
    "confirmed": "确定",
    "uncertain": "暂不能判断",
    "unsupported": "工具暂不支持",
}
_FIELD_STATE_LABELS = {
    "present": "原文明示",
    "not_mentioned": "原文未提及",
    "not_applicable": "本项不适用",
    "uncertain": "暂不能判断",
    "unsupported": "工具暂不支持",
}


def _display_value(
    value: object,
    labels: dict[str, str] | None = None,
    *,
    empty: str = _EMPTY_VALUE,
) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    if value is None:
        return empty
    if isinstance(value, list):
        joined = "、".join(str(item).strip() for item in value if str(item).strip())
        return joined or empty
    if labels is not None and value in labels:
        return labels[value]
    text = str(value).strip()
    return text or empty


def _value_kind(item_id: str, path: str, category: str, field_label: str) -> str:
    if item_id.startswith("coverage:"):
        return "coverage"
    if (
        category == "证据"
        or ".mentions." in path
        or ".evidence." in path
        or path.endswith(".evidence")
        or field_label.startswith("指代")
        or field_label.endswith("证据")
    ):
        return "evidence"
    return "text"


def _correction_hint(value_kind: str, field_label: str) -> str:
    if value_kind == "evidence":
        return "只填正确的原文，例如：承慶。不必填写第几字或起止索引。"
    if value_kind == "coverage":
        return "用一句话说明漏标或误标了什么，不必填索引。"
    return f"填写正确的{field_label}。"


def _evidence_value(evidence: EvidenceSpan) -> str:
    length = evidence.end - evidence.start
    if length <= 1:
        return f"“{evidence.quote}”　第 {evidence.start} 字"
    last = evidence.end - 1
    return f"“{evidence.quote}”　第 {evidence.start}–{last} 字（{length} 字）"


def build_ai_annotation_review_items(
    label: ExtractionAnnotationLabel,
) -> list[AIAnnotationReviewItem]:
    """由首轮标签按填写字段展开检查项，不向 LLM 请求自由检查计划。"""

    drop_meaningless_excluded_mentions(label)
    items: list[AIAnnotationReviewItem] = []

    def add(
        item_id: str,
        category: str,
        path: str,
        field_label: str,
        question: str,
        standard_ids: list[str],
        *,
        group_key: str,
        group_title: str,
        section_key: str,
        section_title: str,
        ai_value: str = "",
        detail: str = "",
        evidence: list[EvidenceSpan] | None = None,
    ) -> None:
        title = f"{group_title} · {section_title} · {field_label}"
        value_kind = _value_kind(item_id, path, category, field_label)
        items.append(
            AIAnnotationReviewItem(
                id=item_id,
                category=category,
                path=path,
                title=title,
                question=question,
                detail=detail,
                field_label=field_label,
                ai_value=ai_value,
                group_key=group_key,
                group_title=group_title,
                section_key=section_key,
                section_title=section_title,
                value_kind=value_kind,
                correction_hint=_correction_hint(value_kind, field_label),
                standard_ids=standard_ids,
                evidence=evidence or [],
            )
        )

    passage = label.passage
    add(
        "passage:source_type",
        "材料",
        "passage.source_type",
        "文献类型",
        "文献类型是否只依据当前标题和正文？",
        ["S01"],
        group_key="passage",
        group_title="文献",
        section_key="passage",
        section_title="文献属性",
        ai_value=_display_value(passage.source_type, _SOURCE_TYPE_LABELS),
    )
    add(
        "passage:material_status",
        "材料",
        "passage.material_status",
        "材料状态",
        "材料状态是否与标题、正文是否完整匹配一致？",
        ["S01", "S16"],
        group_key="passage",
        group_title="文献",
        section_key="passage",
        section_title="文献属性",
        ai_value=_display_value(passage.material_status, _MATERIAL_STATUS_LABELS),
    )
    for field_name, field_label, question in (
        ("is_female", "主人公明示为女性", "主人公是否被原文明示为女性？未提及应保持未判断。"),
        ("is_damaged", "正文含残缺符号", "正文是否含残缺符号？"),
        ("is_clergy", "主人公明示为出家人", "主人公是否被原文明示为出家人？"),
        ("has_courtesy_name", "明确出现字或号", "原文是否明确出现字或号？"),
    ):
        add(
            f"passage:{field_name}",
            "材料",
            f"passage.{field_name}",
            field_label,
            question,
            ["S01", "S04"],
            group_key="passage",
            group_title="文献",
            section_key="passage",
            section_title="文献属性",
            ai_value=_display_value(getattr(passage, field_name), empty="未判断"),
        )
    add(
        "passage:era",
        "材料",
        "passage.era",
        "文献级年号",
        "文献级年号是否原文明示，且未用外部知识补齐？",
        ["S01"],
        group_key="passage",
        group_title="文献",
        section_key="passage",
        section_title="文献属性",
        ai_value=_display_value(passage.era),
    )
    add(
        "passage:era_basis",
        "材料",
        "passage.era_basis",
        "年号选择依据",
        "年号选择依据是否写明原文出处？",
        ["S01"],
        group_key="passage",
        group_title="文献",
        section_key="passage",
        section_title="文献属性",
        ai_value=_display_value(passage.era_basis),
    )
    add(
        "passage:note",
        "材料",
        "passage.note",
        "材料备注",
        "材料备注是否只记录错配、残损或文字疑误，没有额外推断？",
        ["S01", "S15"],
        group_key="passage",
        group_title="文献",
        section_key="passage",
        section_title="文献属性",
        ai_value=_display_value(passage.note),
    )

    for item_id, category, field_label, question, standards in (
        ("coverage:persons", "人物", "全文人物覆盖", "通读全文后，是否还有应建而未建的可识别人物？", ["S02", "S03", "S18"]),
        ("coverage:events", "事件", "全文五类事件覆盖", "逐人检查五类事件后，是否还有应标而未标的事件？", ["S07", "S18"]),
        ("coverage:history", "历史事件", "全文历史事件覆盖", "是否还有原文明示却未标注的人物与历史事件联系？", ["S11", "S18"]),
        ("coverage:relations", "关系", "全文人物关系覆盖", "是否还有原文明示却未标注的人物关系？", ["S12", "S18"]),
        ("coverage:exceptions", "异常", "全文异常与限制覆盖", "是否还有未记录的排除、暂不能判断或工具限制事项？", ["S14", "S15", "S16", "S18"]),
    ):
        add(
            item_id,
            category,
            item_id.replace(":", "."),
            field_label,
            question,
            standards,
            group_key="coverage",
            group_title="覆盖检查",
            section_key="coverage",
            section_title="全文覆盖",
            ai_value="需结合左侧原文判断",
        )

    person_names = {person.key: person.name_surface or person.key for person in label.persons}
    for person_index, person in enumerate(label.persons):
        person_path = f"persons.{person_index}"
        person_name = person.name_surface or person.key
        group_key = f"person:{person.key}"
        group_title = f"L{person.level} · {person_name}"
        person_evidence = list(person.mentions)
        identity_fields = (
            (
                "name_surface",
                "name_surface",
                "原文姓名",
                _display_value(person.name_surface),
                f"“{person_name}”的原文姓名是否只取本篇出现的写法？",
                ["S02", "S04"],
            ),
            (
                "completed_name",
                "completed_name",
                "篇内补全姓名",
                _display_value(person.completed_name),
                "篇内补全姓名是否只依据本篇语法和上下文，没有使用外部知识？",
                ["S04"],
            ),
            (
                "completion_reason",
                "completion_reason",
                "补全理由",
                _display_value(person.completion_reason),
                "补全理由是否说明了本篇内的依据？无补全时应为空。",
                ["S04"],
            ),
            (
                "courtesy_name",
                "courtesy_name",
                "字",
                _display_value(person.courtesy_name),
                "字是否原文明示？未提及应留空。",
                ["S04"],
            ),
            (
                "hao",
                "hao",
                "号",
                _display_value(person.hao),
                "号是否原文明示？未提及应留空。",
                ["S04"],
            ),
            (
                "titles",
                "titles",
                "称号",
                _display_value(person.titles),
                "称号是否只含封号、谥号等，没有把官职混入？",
                ["S04"],
            ),
            (
                "level",
                "level",
                "人物等级",
                f"{person.level} 级",
                f"将“{person_name}”判为 {person.level} 级是否符合叙事中心、强关联和具体作用标准？",
                ["S05"],
            ),
            (
                "level_reason",
                "level_reason",
                "分级理由",
                _display_value(person.level_reason),
                "分级理由是否说明了篇章作用和与主人公的关系？",
                ["S05"],
            ),
        )
        for field_id, path_suffix, field_label, ai_value, question, standards in identity_fields:
            add(
                f"person:{person.key}:{field_id}",
                "人物",
                f"{person_path}.{path_suffix}",
                field_label,
                question,
                standards,
                group_key=group_key,
                group_title=group_title,
                section_key="identity",
                section_title="人物属性",
                ai_value=ai_value,
                evidence=person_evidence,
            )
        for mention_index, evidence in enumerate(person.mentions):
            add(
                f"person:{person.key}:mention:{mention_index}",
                "证据",
                f"{person_path}.mentions.{mention_index}",
                f"指代 {mention_index + 1}",
                f"“{evidence.quote}”是否只覆盖该人物的实体或稳定指代，且位置正确？",
                ["S06", "S17"],
                group_key=group_key,
                group_title=group_title,
                section_key="mentions",
                section_title="人物指代",
                ai_value=_evidence_value(evidence),
                evidence=[evidence],
            )
        if person.level in (1, 2):
            for event_type in EVENT_TYPES:
                state = person.event_checks.get(event_type, "unreviewed")
                matching_event_evidence = [
                    evidence
                    for event in person.life_events
                    if event.event_type == event_type
                    for evidence in event.evidence
                ]
                add(
                    f"person:{person.key}:event-check:{event_type}",
                    "事件",
                    f"{person_path}.event_checks.{event_type}",
                    event_type,
                    f"“{person_name}”的{event_type}检查状态是否与正文及已建事件一致？",
                    ["S07", "S08"],
                    group_key=group_key,
                    group_title=group_title,
                    section_key="event-checks",
                    section_title="五类事件检查",
                    ai_value=_display_value(state, _CHECK_STATE_LABELS),
                    evidence=matching_event_evidence,
                )

        for event_index, event in enumerate(person.life_events):
            event_path = f"{person_path}.life_events.{event_index}"
            section_key = f"event:{event.key}"
            section_title = event.event_type
            if event.official_title:
                section_title = f"{event.event_type} · {event.official_title}"
            elif event.location.raw:
                section_title = f"{event.event_type} · {event.location.raw}"
            elif event.time.raw:
                section_title = f"{event.event_type} · {event.time.raw}"
            event_fields = [
                (
                    "event_type",
                    "event_type",
                    "事件类型",
                    event.event_type,
                    f"该事件是否确属“{person_name}”的{event.event_type}？",
                    ["S08", "S09"],
                ),
                (
                    "state",
                    "state",
                    "事实状态",
                    _display_value(event.state, _FACT_STATE_LABELS),
                    "事实状态是否与原文确定性一致，没有把不确定写成确定？",
                    ["S09", "S15", "S16"],
                ),
                (
                    "time.state",
                    "time.state",
                    "时间状态",
                    _display_value(event.time.state, _FIELD_STATE_LABELS),
                    "时间状态是否正确？籍贯事件的时间应为不适用。",
                    ["S09"],
                ),
                (
                    "time.raw",
                    "time.raw",
                    "原文时间",
                    _display_value(event.time.raw),
                    "原文时间是否照录，没有推断或改写？",
                    ["S09"],
                ),
                (
                    "time.era",
                    "time.era",
                    "年号",
                    _display_value(event.time.era),
                    "事件年号是否原文明示，且没有把文献年号自动抄入？",
                    ["S09"],
                ),
                (
                    "time.era_year",
                    "time.era_year",
                    "年号内年",
                    _display_value(event.time.era_year),
                    "年号内年是否原文明示？",
                    ["S09"],
                ),
                (
                    "time.gregorian_year",
                    "time.gregorian_year",
                    "公元年",
                    _display_value(event.time.gregorian_year),
                    "公元年是否仅由原文明示年号换算，没有额外推断？",
                    ["S09"],
                ),
                (
                    "time.month_text",
                    "time.month_text",
                    "月原文",
                    _display_value(event.time.month_text),
                    "月原文是否照录？",
                    ["S09"],
                ),
                (
                    "time.day_text",
                    "time.day_text",
                    "日原文",
                    _display_value(event.time.day_text),
                    "日原文是否照录？",
                    ["S09"],
                ),
                (
                    "location.state",
                    "location.state",
                    "地点状态",
                    _display_value(event.location.state, _FIELD_STATE_LABELS),
                    "地点状态是否正确？",
                    ["S09"],
                ),
                (
                    "location.raw",
                    "location.raw",
                    "原文地点",
                    _display_value(event.location.raw),
                    "原文地点是否照录，没有用常识补上级政区？",
                    ["S09"],
                ),
                (
                    "location.dao",
                    "location.dao",
                    "道",
                    _display_value(event.location.dao),
                    "道是否原文明示？",
                    ["S09"],
                ),
                (
                    "location.fu",
                    "location.fu",
                    "府",
                    _display_value(event.location.fu),
                    "府是否原文明示？",
                    ["S09"],
                ),
                (
                    "location.zhou",
                    "location.zhou",
                    "州",
                    _display_value(event.location.zhou),
                    "州是否原文明示？",
                    ["S09"],
                ),
                (
                    "location.jun",
                    "location.jun",
                    "郡",
                    _display_value(event.location.jun),
                    "郡是否原文明示？",
                    ["S09"],
                ),
                (
                    "location.xian",
                    "location.xian",
                    "县",
                    _display_value(event.location.xian),
                    "县是否原文明示？",
                    ["S09"],
                ),
                (
                    "location.other",
                    "location.other",
                    "其他地点",
                    _display_value(event.location.other),
                    "其他地点是否原文明示？",
                    ["S09"],
                ),
            ]
            if event.event_type == "任职" or event.official_title:
                event_fields.append(
                    (
                        "official_title",
                        "official_title",
                        "完整官职",
                        _display_value(event.official_title),
                        "完整官职是否保留行、守、试、检校、兼、赠等限定词，且仅任职填写？",
                        ["S09"],
                    )
                )
            event_fields.append(
                (
                    "note",
                    "note",
                    "事件备注",
                    _display_value(event.note),
                    "事件备注是否只补充必要说明，没有写入推断事实？",
                    ["S09", "S15"],
                )
            )
            for field_id, path_suffix, field_label, ai_value, question, standards in event_fields:
                add(
                    f"event:{person.key}:{event.key}:{field_id}",
                    "事件",
                    f"{event_path}.{path_suffix}",
                    field_label,
                    question,
                    standards,
                    group_key=group_key,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value=ai_value,
                    evidence=list(event.evidence),
                )
            if event.evidence:
                for evidence_index, evidence in enumerate(event.evidence):
                    add(
                        f"event:{person.key}:{event.key}:evidence:{evidence_index}",
                        "证据",
                        f"{event_path}.evidence.{evidence_index}",
                        f"证据 {evidence_index + 1}",
                        "该证据是否为最小充分事实短语，且能证明人物、事件内容与归属？",
                        ["S10", "S17"],
                        group_key=group_key,
                        group_title=group_title,
                        section_key=section_key,
                        section_title=section_title,
                        ai_value=_evidence_value(evidence),
                        evidence=[evidence],
                    )
            else:
                add(
                    f"event:{person.key}:{event.key}:evidence",
                    "证据",
                    f"{event_path}.evidence",
                    "事件证据",
                    "该事件是否缺少可回放的最小充分事实证据？",
                    ["S10", "S17"],
                    group_key=group_key,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value="无正文证据",
                )

        for event_index, event in enumerate(person.historical_events):
            event_path = f"{person_path}.historical_events.{event_index}"
            section_key = f"history:{event.key}"
            section_title = f"历史事件 · {event.event_name or '未命名'}"
            history_fields = (
                (
                    "event_name",
                    "event_name",
                    "标准事件名称",
                    _display_value(event.event_name),
                    "历史事件名称是否正文明确出现，并优先使用标准名称表？",
                    ["S11"],
                ),
                (
                    "outside_dictionary",
                    "outside_dictionary",
                    "名称表外事件",
                    _display_value(event.outside_dictionary),
                    "表外勾选是否与名称是否在字典中一致？",
                    ["S11"],
                ),
                (
                    "relation_summary",
                    "relation_summary",
                    "关系说明",
                    _display_value(event.relation_summary),
                    "人物与该历史事件的作用说明是否原文明示且不超过 15 字？",
                    ["S11"],
                ),
                (
                    "state",
                    "state",
                    "事实状态",
                    _display_value(event.state, _FACT_STATE_LABELS),
                    "历史事件事实状态是否正确？",
                    ["S11", "S15", "S16"],
                ),
                (
                    "note",
                    "note",
                    "备注",
                    _display_value(event.note),
                    "历史事件备注是否只补充必要说明？",
                    ["S11"],
                ),
            )
            for field_id, path_suffix, field_label, ai_value, question, standards in history_fields:
                add(
                    f"history:{person.key}:{event.key}:{field_id}",
                    "历史事件",
                    f"{event_path}.{path_suffix}",
                    field_label,
                    question,
                    standards,
                    group_key=group_key,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value=ai_value,
                    evidence=list(event.evidence),
                )
            if event.evidence:
                for evidence_index, evidence in enumerate(event.evidence):
                    add(
                        f"history:{person.key}:{event.key}:evidence:{evidence_index}",
                        "证据",
                        f"{event_path}.evidence.{evidence_index}",
                        f"证据 {evidence_index + 1}",
                        "该证据是否同时支持人物、事件和联系？",
                        ["S10", "S11", "S17"],
                        group_key=group_key,
                        group_title=group_title,
                        section_key=section_key,
                        section_title=section_title,
                        ai_value=_evidence_value(evidence),
                        evidence=[evidence],
                    )
            else:
                add(
                    f"history:{person.key}:{event.key}:evidence",
                    "证据",
                    f"{event_path}.evidence",
                    "关联证据",
                    "该历史事件关联是否缺少可回放证据？",
                    ["S10", "S11", "S17"],
                    group_key=group_key,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value="无正文证据",
                )

    for relation_index, relation in enumerate(label.person_relations):
        source = person_names.get(relation.source_person_key, relation.source_person_key)
        target = person_names.get(relation.target_person_key, relation.target_person_key)
        relation_path = f"person_relations.{relation_index}"
        section_key = f"relation:{relation.key}"
        section_title = f"{source} 与 {target}"
        relation_fields = (
            (
                "source_person_key",
                "source_person_key",
                "关系人物一",
                source,
                "关系第一端人物是否正确？",
                ["S12"],
            ),
            (
                "target_person_key",
                "target_person_key",
                "关系人物二",
                target,
                "关系第二端人物是否正确？",
                ["S12"],
            ),
            (
                "relation_category",
                "relation_category",
                "关系类别",
                _display_value(relation.relation_category),
                "关系类别是否属于七类之一，且符合原文语义？",
                ["S12"],
            ),
            (
                "relation_summary",
                "relation_summary",
                "具体表现",
                _display_value(relation.relation_summary),
                "10 字内的具体表现是否说明两人关系并得到原文支持？",
                ["S12"],
            ),
            (
                "state",
                "state",
                "关系状态",
                _display_value(relation.state, _FACT_STATE_LABELS),
                "关系事实状态是否正确，且不是由外部知识或单纯共现推出？",
                ["S12", "S15", "S16"],
            ),
        )
        for field_id, path_suffix, field_label, ai_value, question, standards in relation_fields:
            add(
                f"relation:{relation.key}:{field_id}",
                "关系",
                f"{relation_path}.{path_suffix}",
                field_label,
                question,
                standards,
                group_key="relations",
                group_title="人物关系",
                section_key=section_key,
                section_title=section_title,
                ai_value=ai_value,
                evidence=list(relation.evidence),
            )
        if relation.evidence:
            for evidence_index, evidence in enumerate(relation.evidence):
                add(
                    f"relation:{relation.key}:evidence:{evidence_index}",
                    "证据",
                    f"{relation_path}.evidence.{evidence_index}",
                    f"证据 {evidence_index + 1}",
                    "该证据是否同时支持两端人物和关系触发语？",
                    ["S13", "S17"],
                    group_key="relations",
                    group_title="人物关系",
                    section_key=section_key,
                    section_title=section_title,
                    ai_value=_evidence_value(evidence),
                    evidence=[evidence],
                )
        else:
            add(
                f"relation:{relation.key}:evidence",
                "证据",
                f"{relation_path}.evidence",
                "关系证据",
                "该关系是否缺少可回放证据？",
                ["S13", "S17"],
                group_key="relations",
                group_title="人物关系",
                section_key=section_key,
                section_title=section_title,
                ai_value="无正文证据",
            )

    auxiliary = (
        ("excluded_mentions", "排除项", "排除称谓", label.excluded_mentions, ["S14", "S06", "S17"]),
        ("unresolved_items", "待判断", "暂不能判断", label.unresolved_items, ["S15", "S17"]),
        ("schema_conflicts", "工具限制", "工具限制", label.schema_conflicts, ["S16", "S17"]),
    )
    for field, category, group_title, values, standards in auxiliary:
        for item_index, value in enumerate(values):
            section_key = f"{field}:{value.key}"
            section_title = f"{category} {item_index + 1}"
            aux_fields: list[tuple[str, str, str, str, str]] = []
            if hasattr(value, "reason"):
                aux_fields.append(
                    ("reason", "reason", "排除原因", _display_value(getattr(value, "reason")), f"该{category}的原因是否正确且必要？")
                )
            if hasattr(value, "category"):
                aux_fields.append(
                    ("category", "category", "分类", _display_value(getattr(value, "category")), f"该{category}的分类是否正确？")
                )
            if hasattr(value, "code"):
                aux_fields.append(
                    ("code", "code", "限制代码", _display_value(getattr(value, "code")), "工具限制代码是否正确区分不确定与 Schema 不支持？")
                )
            if hasattr(value, "note"):
                aux_fields.append(
                    ("note", "note", "说明", _display_value(getattr(value, "note")), f"该{category}的说明是否准确？")
                )
            for field_id, path_suffix, field_label, ai_value, question in aux_fields:
                add(
                    f"aux:{field}:{value.key}:{field_id}",
                    "异常",
                    f"{field}.{item_index}.{path_suffix}",
                    field_label,
                    question,
                    standards,
                    group_key=field,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value=ai_value,
                    evidence=list(value.evidence),
                )
            if value.evidence:
                for evidence_index, evidence in enumerate(value.evidence):
                    add(
                        f"aux:{field}:{value.key}:evidence:{evidence_index}",
                        "证据",
                        f"{field}.{item_index}.evidence.{evidence_index}",
                        f"证据 {evidence_index + 1}",
                        f"该{category}证据是否粒度合适且位置正确？",
                        standards,
                        group_key=field,
                        group_title=group_title,
                        section_key=section_key,
                        section_title=section_title,
                        ai_value=_evidence_value(evidence),
                        evidence=[evidence],
                    )
            else:
                add(
                    f"aux:{field}:{value.key}:evidence",
                    "证据",
                    f"{field}.{item_index}.evidence",
                    "原文证据",
                    f"该{category}是否缺少可回放证据？",
                    standards,
                    group_key=field,
                    group_title=group_title,
                    section_key=section_key,
                    section_title=section_title,
                    ai_value="无正文证据",
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

    def start_review(self, job_id: int, current_user: User) -> AIAnnotationReviewResponse:
        """在用户实际进入质检页时幂等记录人工质检开始时间。"""

        job, result, passage = self._review_context(job_id, current_user)
        row = self._get_row(job.id)
        if row is None:
            if self._blind_submission_locked(job.task_id, current_user.id):
                raise ExtractionAIAnnotationReviewError(
                    "该篇盲标已提交，不能再开始首轮质检",
                    status_code=409,
                )
            items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
            decisions = [AIAnnotationReviewDecision(item_id=item.id) for item in items]
            row = self._write_row(
                job,
                current_user.id,
                row,
                decisions,
                [],
                self._summary(decisions, []),
            )
        elif row.started_at is None:
            row.started_at = row.created_at or _now()
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return self._to_response(job, result, passage, current_user.id, row)

    def save_review(
        self,
        job_id: int,
        payload: AIAnnotationReviewUpdateRequest,
        current_user: User,
    ) -> AIAnnotationReviewResponse:
        job, result, passage = self._review_context(job_id, current_user)
        row = self._get_row(job.id)
        if self._blind_submission_locked(job.task_id, current_user.id):
            raise ExtractionAIAnnotationReviewError(
                "该篇盲标已提交，首轮质检已锁定",
                status_code=409,
            )
        items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
        decisions, omissions = self._normalize_update(payload, items, passage)
        summary = self._summary(decisions, omissions)
        if row is not None and row.status == "submitted":
            # 质检提交是阶段性快照；盲标尚未提交时，修改后回到草稿状态。
            # submitted_at 保留上次成功提交时间，再次提交时覆盖。
            row.status = "draft"
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
        blind_locked = self._blind_submission_locked(job.task_id, current_user.id)
        if row is not None and row.status == "submitted" and blind_locked:
            return self._to_response(job, result, passage, current_user.id, row)
        if blind_locked:
            raise ExtractionAIAnnotationReviewError(
                "该篇盲标已提交，首轮质检已锁定",
                status_code=409,
            )
        items = build_ai_annotation_review_items(result.document.label)  # type: ignore[union-attr]
        decisions, omissions = self._normalize_update(payload, items, passage)
        self._validate_submission(decisions, omissions, items)
        self._validate_review_application(
            result.document.label,  # type: ignore[union-attr]
            items,
            decisions,
            omissions,
            passage.context,
        )
        summary = self._summary(decisions, omissions)
        row = self._write_row(
            job,
            current_user.id,
            row,
            decisions,
            omissions,
            summary,
            commit=False,
        )
        row.status = "submitted"
        submitted_at = _now()
        row.submitted_at = submitted_at
        row.updated_at = submitted_at
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
            result = parse_ai_annotation_result(json.loads(job.result_json))
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionAIAnnotationReviewError("首轮 AI 结果无法读取", status_code=409) from exc
        if result.document is None:
            raise ExtractionAIAnnotationReviewError("首轮 AI 结果无法解析为标注对象，需重新生成", status_code=409)
        drop_meaningless_excluded_mentions(result.document.label)
        if result.label is not None:
            drop_meaningless_excluded_mentions(result.label)
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
        row = (
            self.db.query(ExtractionAIAnnotationReview)
            .filter(ExtractionAIAnnotationReview.generation_job_id == generation_job_id)
            .first()
        )
        if row is not None and row.status == "submitted" and row.submitted_at is None:
            # 修复旧版本可能遗留的“已提交但没有提交时间”记录。
            row.submitted_at = row.updated_at or row.created_at or _now()
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    def _blind_submission(
        self,
        task_id: int,
        reviewer_id: int,
    ) -> ExtractionAnnotationSubmission | None:
        return (
            self.db.query(ExtractionAnnotationSubmission)
            .filter(
                ExtractionAnnotationSubmission.task_id == task_id,
                ExtractionAnnotationSubmission.annotator_id == reviewer_id,
            )
            .first()
        )

    def _blind_submission_locked(self, task_id: int, reviewer_id: int) -> bool:
        submission = self._blind_submission(task_id, reviewer_id)
        return bool(
            submission is not None
            and (submission.state == "submitted" or submission.submitted_at is not None)
        )

    def _normalize_update(
        self,
        payload: AIAnnotationReviewUpdateRequest,
        items: list[AIAnnotationReviewItem],
        passage: Passage,
    ) -> tuple[list[AIAnnotationReviewDecision], list[AIAnnotationReviewOmission]]:
        valid_ids = {item.id for item in items}
        item_by_id = {item.id: item for item in items}
        seen: set[str] = set()
        submitted_by_id: dict[str, AIAnnotationReviewDecision] = {}
        for decision in payload.decisions:
            if decision.item_id in seen:
                raise ExtractionAIAnnotationReviewError("质检项不能重复提交", status_code=422)
            if decision.item_id not in valid_ids:
                raise ExtractionAIAnnotationReviewError("质检项与当前首轮结果不一致，请刷新页面", status_code=409)
            seen.add(decision.item_id)
            current_item = item_by_id[decision.item_id]
            if decision.remove_item:
                from app.services.extraction_ai_annotation_review_apply import (
                    review_removal_path,
                )

                if review_removal_path(current_item) is None:
                    raise ExtractionAIAnnotationReviewError(
                        "该字段不属于可删除的人物、事件、关系、异常项或证据",
                        status_code=422,
                    )
            submitted_by_id[decision.item_id] = decision

        decisions: list[AIAnnotationReviewDecision] = []
        for current_item in items:
            submitted = submitted_by_id.get(current_item.id)
            if submitted is None:
                decisions.append(AIAnnotationReviewDecision(item_id=current_item.id))
                continue

            remove_item = bool(submitted.remove_item)
            verdict = "incorrect" if remove_item else submitted.verdict
            reason = submitted.reason.strip()
            correction = submitted.correction.strip()
            correction_evidence = (
                list(submitted.correction_evidence) if verdict == "incorrect" else []
            )
            correction_data = (
                dict(submitted.correction_data) if verdict == "incorrect" else {}
            )
            if remove_item:
                reason = reason or "AI 误生成该记录，质检后移除。"
                correction = ""
                correction_evidence = []
                correction_data = {}
            elif verdict == "correct":
                reason = ""
                correction = ""
                correction_data = {}
            elif (
                verdict == "incorrect"
                and not correction
                and correction_evidence
                and current_item.value_kind == "evidence"
            ):
                correction = " / ".join(item.quote for item in correction_evidence)
            if not (
                verdict == "incorrect"
                and ":event-check:" in current_item.id
                and correction in {"已检查且有事实", "has_fact"}
            ):
                correction_data = {}
            self._validate_correction_evidence(
                current_item,
                correction_evidence,
                passage.context,
            )
            decisions.append(
                AIAnnotationReviewDecision(
                    item_id=current_item.id,
                    verdict=verdict,  # type: ignore[arg-type]
                    reason=reason,
                    correction=correction,
                    correction_evidence=correction_evidence,
                    correction_data=correction_data,
                    remove_item=remove_item,
                )
            )

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
                addition_type=omission.addition_type,
                parent_key=omission.parent_key.strip(),
                data=dict(omission.data),
            )
            self._validate_omission_evidence(normalized, passage.context)
            omissions.append(normalized)
        return decisions, omissions

    @classmethod
    def _validate_span(
        cls,
        evidence: EvidenceSpan,
        context: str,
        *,
        kind: Literal["entity", "fact"],
        label: str,
    ) -> None:
        if evidence.end > len(context) or context[evidence.start : evidence.end] != evidence.quote:
            raise ExtractionAIAnnotationReviewError(f"{label}与当前正文位置不一致", status_code=422)
        if (
            evidence.start_utf16 != codepoint_to_utf16_offset(context, evidence.start)
            or evidence.end_utf16 != codepoint_to_utf16_offset(context, evidence.end)
        ):
            raise ExtractionAIAnnotationReviewError(f"{label}的 UTF-16 位置不一致", status_code=422)
        granularity_issue = evidence_granularity_issue(evidence, kind)
        if granularity_issue is not None:
            raise ExtractionAIAnnotationReviewError(
                f"{label}粒度过大：{granularity_issue[1]}",
                status_code=422,
            )

    @classmethod
    def _validate_omission_evidence(cls, omission: AIAnnotationReviewOmission, context: str) -> None:
        target_type = str(omission.data.get("target_type") or "")
        kind: Literal["entity", "fact"] = (
            "entity"
            if omission.addition_type in {"person", "excluded_mention"}
            or (omission.addition_type == "evidence" and target_type in {"person", "excluded_mention"})
            else "fact"
        )
        for evidence in omission.evidence:
            cls._validate_span(evidence, context, kind=kind, label="补录记录的原文证据")

    @classmethod
    def _validate_correction_evidence(
        cls,
        item: AIAnnotationReviewItem,
        evidence_list: list[EvidenceSpan],
        context: str,
    ) -> None:
        kind: Literal["entity", "fact"] = (
            "entity" if ".mentions." in item.path or item.field_label.startswith("指代") else "fact"
        )
        for evidence in evidence_list:
            cls._validate_span(evidence, context, kind=kind, label="错误项的划选证据")

    @staticmethod
    def _validate_submission(
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
        items: list[AIAnnotationReviewItem],
    ) -> None:
        unreviewed = [item for item in decisions if item.verdict == "unreviewed"]
        if unreviewed:
            raise ExtractionAIAnnotationReviewError(
                f"还有 {len(unreviewed)} 个检查项未判断，不能提交质检",
                status_code=422,
            )
        missing_corrections = [
            item
            for item in decisions
            if item.verdict == "incorrect"
            and not item.item_id.startswith("coverage:")
            and not item.remove_item
            and not item.correction
        ]
        if missing_corrections:
            raise ExtractionAIAnnotationReviewError(
                f"有 {len(missing_corrections)} 个错误项尚未填写正确值",
                status_code=422,
            )
        coverage_categories = {
            "coverage:persons": {"人物缺漏", "等级缺漏", "属性缺漏"},
            "coverage:events": {"事件缺漏", "时间缺漏", "地点缺漏", "官职缺漏"},
            "coverage:history": {"历史事件缺漏"},
            "coverage:relations": {"关系缺漏"},
            "coverage:exceptions": {"其他缺漏", "结构增补"},
        }
        for decision in decisions:
            if decision.item_id not in coverage_categories:
                continue
            matching = [
                omission for omission in omissions
                if omission.category in coverage_categories[decision.item_id]
                and omission.target_path and omission.description
                and (omission.evidence or omission.evidence_note)
            ]
            if decision.verdict == "incorrect" and not matching:
                raise ExtractionAIAnnotationReviewError(
                    "覆盖检查发现缺漏时，须在右侧逐条登记类型、对象、原文证据和应补位置",
                    status_code=422,
                )
            if decision.verdict == "correct" and matching:
                raise ExtractionAIAnnotationReviewError(
                    "覆盖检查标为无缺漏，但右侧存在对应缺漏记录，请统一检查结果",
                    status_code=422,
                )
        missing_event_evidence = [
            item
            for item in decisions
            if item.verdict == "incorrect"
            and ":event-check:" in item.item_id
            and item.correction in {"已检查且有事实", "has_fact"}
            and not item.correction_evidence
        ]
        if missing_event_evidence:
            raise ExtractionAIAnnotationReviewError(
                f"有 {len(missing_event_evidence)} 个新增事件事实尚未划选原文证据",
                status_code=422,
            )
        item_by_id = {item.id: item for item in items}
        new_event_decisions = [
            decision
            for decision in decisions
            if decision.verdict == "incorrect"
            and ":event-check:" in decision.item_id
            and decision.correction in {"已检查且有事实", "has_fact"}
            and item_by_id.get(decision.item_id) is not None
            and item_by_id[decision.item_id].ai_value in {"原文未提及", "not_mentioned"}
        ]
        missing_event_details = [
            decision for decision in new_event_decisions if not decision.correction_data
        ]
        if missing_event_details:
            raise ExtractionAIAnnotationReviewError(
                f"有 {len(missing_event_details)} 个新增事件事实尚未填写完整事件卡片",
                status_code=422,
            )
        for decision in new_event_decisions:
            item = item_by_id[decision.item_id]
            event_type = item.path.rsplit(".", 1)[-1]
            event_payload = dict(decision.correction_data)
            event_payload["event_type"] = event_type
            event_payload["evidence"] = [
                evidence.model_dump(mode="python")
                for evidence in decision.correction_evidence
            ]
            try:
                event = LifeEventAnnotation.model_validate(event_payload)
            except ValidationError as exc:
                raise ExtractionAIAnnotationReviewError(
                    f"{event_type}新增事件卡片信息不完整或格式错误",
                    status_code=422,
                ) from exc
            if event_type == "任职" and not event.official_title.strip():
                raise ExtractionAIAnnotationReviewError(
                    "任职新增事件必须填写完整官职",
                    status_code=422,
                )
            if event_type == "籍贯" and event.time.state != "not_applicable":
                raise ExtractionAIAnnotationReviewError(
                    "籍贯新增事件的时间状态必须为不适用",
                    status_code=422,
                )
            if event_type != "任职" and event.official_title.strip():
                raise ExtractionAIAnnotationReviewError(
                    f"{event_type}新增事件不能填写官职",
                    status_code=422,
                )
        for index, omission in enumerate(omissions, start=1):
            if omission.addition_type != "note":
                ExtractionAIAnnotationReviewService._validate_structured_addition(
                    omission,
                    index,
                )
                continue
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
    def _validate_structured_addition(
        omission: AIAnnotationReviewOmission,
        index: int,
    ) -> None:
        data = omission.data
        label = f"第 {index} 条结构补录"
        if omission.addition_type == "person":
            if not str(data.get("name_surface") or "").strip():
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写人物姓名", status_code=422)
            if not str(data.get("level_reason") or "").strip():
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写人物分级理由", status_code=422)
        elif omission.addition_type in {"life_event", "historical_event"}:
            if not omission.parent_key:
                raise ExtractionAIAnnotationReviewError(f"{label}必须选择所属人物", status_code=422)
            if omission.addition_type == "life_event":
                if data.get("event_type") not in EVENT_TYPES:
                    raise ExtractionAIAnnotationReviewError(f"{label}必须选择事件类型", status_code=422)
                if data.get("event_type") == "任职" and not str(data.get("official_title") or "").strip():
                    raise ExtractionAIAnnotationReviewError(f"{label}的任职事件必须填写官职", status_code=422)
            else:
                if not str(data.get("event_name") or "").strip():
                    raise ExtractionAIAnnotationReviewError(f"{label}必须填写历史事件名称", status_code=422)
                if not str(data.get("relation_summary") or "").strip():
                    raise ExtractionAIAnnotationReviewError(f"{label}必须填写人物与历史事件的关系", status_code=422)
        elif omission.addition_type == "person_relation":
            source = str(data.get("source_person_key") or "")
            target = str(data.get("target_person_key") or "")
            category = str(data.get("relation_category") or "")
            summary = str(data.get("relation_summary") or "").strip()
            if not source or not target or source == target:
                raise ExtractionAIAnnotationReviewError(f"{label}必须选择两个不同人物", status_code=422)
            if category not in RELATION_CATEGORIES:
                raise ExtractionAIAnnotationReviewError(f"{label}必须选择七类人物关系之一", status_code=422)
            if not summary or len(summary) > 10:
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写不超过 10 字的具体表现", status_code=422)
        elif omission.addition_type == "evidence":
            if not str(data.get("target_type") or "") or not str(data.get("target_key") or ""):
                raise ExtractionAIAnnotationReviewError(f"{label}必须选择证据所属记录", status_code=422)
        elif omission.addition_type == "excluded_mention":
            if not str(data.get("reason") or "").strip():
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写排除原因", status_code=422)
        elif omission.addition_type == "unresolved_item":
            if not str(data.get("category") or "").strip() or not str(data.get("note") or "").strip():
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写分类和说明", status_code=422)
        elif omission.addition_type == "schema_conflict":
            if not str(data.get("code") or "").strip() or not str(data.get("note") or "").strip():
                raise ExtractionAIAnnotationReviewError(f"{label}必须填写限制代码和说明", status_code=422)
        if not omission.evidence:
            if not omission.evidence_note.strip():
                raise ExtractionAIAnnotationReviewError(
                    f"{label}必须划选原文证据；无法划选时填写原文位置说明",
                    status_code=422,
                )

    @staticmethod
    def _summary(
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
    ) -> AIAnnotationReviewSummary:
        scored_decisions = [item for item in decisions if not item.item_id.startswith("coverage:")]
        correct = sum(item.verdict == "correct" for item in scored_decisions)
        incorrect = sum(item.verdict == "incorrect" for item in scored_decisions)
        unreviewed = sum(item.verdict == "unreviewed" for item in scored_decisions)
        denominator = correct + incorrect
        accuracy = round(correct / denominator, 4) if denominator else 0.0
        return AIAnnotationReviewSummary(
            total_item_count=len(scored_decisions),
            reviewed_item_count=correct + incorrect,
            correct_count=correct,
            incorrect_count=incorrect,
            unreviewed_count=unreviewed,
            omission_count=len(omissions),
            accuracy=accuracy,
        )

    @staticmethod
    def _validate_review_application(
        label: ExtractionAnnotationLabel,
        items: list[AIAnnotationReviewItem],
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
        context: str,
    ) -> None:
        from app.services.extraction_ai_annotation_review_apply import apply_ai_review_to_label

        decision_by_id = {decision.item_id: decision for decision in decisions}
        for item in items:
            decision = decision_by_id[item.id]
            item.verdict = decision.verdict
            item.reason = decision.reason
            item.correction = decision.correction
            item.correction_evidence = list(decision.correction_evidence)
            item.correction_data = dict(decision.correction_data)
            item.remove_item = decision.remove_item
        try:
            apply_ai_review_to_label(label, items, omissions, context)
        except (ValidationError, ValueError, IndexError, KeyError, TypeError) as exc:
            raise ExtractionAIAnnotationReviewError(
                f"结构修订无法应用：{exc}",
                status_code=422,
            ) from exc

    def _write_row(
        self,
        job: ExtractionAIAnnotationJob,
        reviewer_id: int,
        row: ExtractionAIAnnotationReview | None,
        decisions: list[AIAnnotationReviewDecision],
        omissions: list[AIAnnotationReviewOmission],
        summary: AIAnnotationReviewSummary,
        *,
        commit: bool = True,
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
                started_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
            )
        elif row.started_at is None:
            row.started_at = row.created_at or timestamp
        row.standard_version = AI_ANNOTATION_REVIEW_STANDARD_VERSION
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
        if commit:
            self.db.commit()
            self.db.refresh(row)
        else:
            # submit_review 会把草稿内容、锁定状态和提交时间放在同一事务提交。
            self.db.flush()
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
                item.correction = decision.correction
                item.correction_evidence = list(decision.correction_evidence)
                item.correction_data = dict(decision.correction_data)
                item.remove_item = decision.remove_item

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
                correction=item.correction,
                correction_evidence=list(item.correction_evidence),
                correction_data=dict(item.correction_data),
                remove_item=item.remove_item,
            )
            for item in items
        ]
        summary = self._summary(normalized_decisions, omissions)
        ground_truth_document = None
        if result.document is not None:
            from app.services.extraction_ai_annotation_review_apply import apply_ai_review_to_label

            try:
                ground_truth_document = AIAnnotationExportDocument(
                    metadata=result.document.metadata,
                    passage=result.document.passage,
                    label=apply_ai_review_to_label(
                        result.document.label,
                        items,
                        omissions,
                        passage.context,
                    ),
                )
            except (ValidationError, ValueError, IndexError, KeyError, TypeError):
                ground_truth_document = None
        blind_submission = self._blind_submission(job.task_id, reviewer_id)
        blind_locked = bool(
            blind_submission is not None
            and (
                blind_submission.state == "submitted"
                or blind_submission.submitted_at is not None
            )
        )
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
            ground_truth_document=ground_truth_document,
            reviewer_id=reviewer_id,
            editable=not blind_locked,
            blind_submitted_at=(
                blind_submission.submitted_at if blind_submission is not None else None
            ),
            started_at=row.started_at if row is not None else None,
            created_at=row.created_at if row is not None else None,
            updated_at=row.updated_at if row is not None else None,
            submitted_at=row.submitted_at if row is not None else None,
        )
