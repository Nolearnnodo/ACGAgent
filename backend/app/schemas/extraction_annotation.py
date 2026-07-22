"""功能 A 知识抽取人工标注的请求与响应 Schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ANNOTATION_SPEC_VERSION = "0.2.0"
EVENT_TYPES = ("出生", "籍贯", "死亡", "埋葬", "任职")

EventType = Literal["出生", "籍贯", "死亡", "埋葬", "任职"]
CheckState = Literal["unreviewed", "has_fact", "not_mentioned", "uncertain", "unsupported"]
FactState = Literal["confirmed", "uncertain", "unsupported"]
FieldState = Literal["present", "not_mentioned", "not_applicable", "uncertain", "unsupported"]
RelationCode = Literal["F", "M", "S", "D", "H", "W", "Z", "C", "B", "O"]


def default_event_checks() -> dict[str, CheckState]:
    return {event_type: "unreviewed" for event_type in EVENT_TYPES}


class EvidenceSpan(BaseModel):
    """正文中的一个连续证据区间。

    start/end 使用 Python Unicode code point；start_utf16/end_utf16 保存浏览器组件偏移，
    服务端会同时校验两套坐标和 quote。
    """

    id: str = Field(min_length=1, max_length=64)
    source: Literal["context"] = "context"
    quote: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    start_utf16: int = Field(ge=0)
    end_utf16: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("证据区间 end 必须大于 start。")
        if self.end_utf16 <= self.start_utf16:
            raise ValueError("证据 UTF-16 区间 end 必须大于 start。")
        return self


class PassageAnnotation(BaseModel):
    source_type: Literal["epitaph", "history", "uncertain"] = "uncertain"
    material_status: Literal["complete", "issue", "uncertain"] = "complete"
    is_female: bool | None = None
    is_damaged: bool | None = None
    is_clergy: bool | None = None
    has_courtesy_name: bool | None = None
    era: str = Field(default="", max_length=64)
    era_basis: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=2000)


class EventTime(BaseModel):
    state: FieldState = "not_mentioned"
    raw: str = Field(default="", max_length=255)
    era: str = Field(default="", max_length=64)
    era_year: int | None = Field(default=None, ge=1, le=999)
    gregorian_year: int | None = Field(default=None, ge=-5000, le=5000)
    month_text: str = Field(default="", max_length=32)
    day_text: str = Field(default="", max_length=32)


class EventLocation(BaseModel):
    state: FieldState = "not_mentioned"
    raw: str = Field(default="", max_length=255)
    dao: str = Field(default="", max_length=64)
    fu: str = Field(default="", max_length=64)
    zhou: str = Field(default="", max_length=64)
    jun: str = Field(default="", max_length=64)
    xian: str = Field(default="", max_length=64)
    other: str = Field(default="", max_length=128)


class LifeEventAnnotation(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    event_type: EventType
    state: FactState = "confirmed"
    time: EventTime = Field(default_factory=EventTime)
    location: EventLocation = Field(default_factory=EventLocation)
    official_title: str = Field(default="", max_length=255)
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=1000)


class HistoricalEventAnnotation(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    event_name: str = Field(default="", max_length=255)
    outside_dictionary: bool = False
    relation_summary: str = Field(default="", max_length=15)
    state: FactState = "confirmed"
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=1000)


class PersonAnnotation(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name_surface: str = Field(default="", max_length=255)
    completed_name: str = Field(default="", max_length=255)
    completion_reason: str = Field(default="", max_length=500)
    courtesy_name: str = Field(default="", max_length=128)
    hao: str = Field(default="", max_length=128)
    titles: list[str] = Field(default_factory=list, max_length=20)
    level: Literal[1, 2, 3] = 3
    level_reason: str = Field(default="", max_length=500)
    mentions: list[EvidenceSpan] = Field(default_factory=list, max_length=50)
    event_checks: dict[str, CheckState] = Field(default_factory=default_event_checks)
    life_events: list[LifeEventAnnotation] = Field(default_factory=list, max_length=100)
    historical_events: list[HistoricalEventAnnotation] = Field(default_factory=list, max_length=50)


class PersonRelationAnnotation(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    source_person_key: str = Field(min_length=1, max_length=64)
    target_person_key: str = Field(min_length=1, max_length=64)
    codes: list[RelationCode] = Field(default_factory=list, max_length=3)
    reverse_codes: list[RelationCode] = Field(default_factory=list, max_length=3)
    note: str = Field(default="", max_length=15)
    reverse_note: str = Field(default="", max_length=15)
    state: FactState = "confirmed"
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)


class ExcludedMention(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=255)
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)


class UnresolvedItem(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    category: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=1000)
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)


class SchemaConflict(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    code: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=1000)
    evidence: list[EvidenceSpan] = Field(default_factory=list, max_length=8)


class ExtractionAnnotationLabel(BaseModel):
    schema_version: str = Field(default=ANNOTATION_SPEC_VERSION, max_length=32)
    passage: PassageAnnotation = Field(default_factory=PassageAnnotation)
    persons: list[PersonAnnotation] = Field(default_factory=list, max_length=500)
    person_relations: list[PersonRelationAnnotation] = Field(default_factory=list, max_length=500)
    excluded_mentions: list[ExcludedMention] = Field(default_factory=list, max_length=500)
    unresolved_items: list[UnresolvedItem] = Field(default_factory=list, max_length=500)
    schema_conflicts: list[SchemaConflict] = Field(default_factory=list, max_length=100)


class ExtractionTaskCreateRequest(BaseModel):
    passage_id: int = Field(gt=0)
    required_annotation_count: int = Field(default=2, ge=1, le=2)
    priority: int = Field(default=0, ge=-100, le=100)
    spec_version: str = Field(default=ANNOTATION_SPEC_VERSION, min_length=1, max_length=32)


class ExtractionTaskSummaryResponse(BaseModel):
    id: int
    passage_id: int
    passage_title: str
    context_sha256: str
    spec_version: str
    status: str
    priority: int
    required_annotation_count: int
    claimed_count: int
    submitted_count: int
    available_slots: int
    submission_id: int | None
    submission_state: str | None
    slot_no: int | None
    revision: int | None
    updated_at: datetime


class AnnotationPassageResponse(BaseModel):
    doc_id: int
    title: str
    context: str
    source_type: str
    workflow_status: str


class ExtractionSubmissionResponse(BaseModel):
    id: int
    task_id: int
    slot_no: int
    state: str
    revision: int
    label: ExtractionAnnotationLabel
    submitted_at: datetime | None
    updated_at: datetime


class ExtractionTaskDetailResponse(BaseModel):
    id: int
    context_sha256: str
    spec_version: str
    status: str
    required_annotation_count: int
    passage: AnnotationPassageResponse
    submission: ExtractionSubmissionResponse


class ExtractionDraftUpdateRequest(BaseModel):
    revision: int = Field(ge=0)
    label: ExtractionAnnotationLabel


class ExtractionSubmitRequest(ExtractionDraftUpdateRequest):
    pass


class AnnotationEraEntry(BaseModel):
    era: str
    dynasty: str
    start_year: int
    end_year: int
    simplified: str
    traditional: str


class AnnotationHistoricalEventEntry(BaseModel):
    id: int
    year_label: str
    event_name: str
    event_details: str
    start_year: int | None
    end_year: int | None


class AnnotationRelationCodeEntry(BaseModel):
    code: RelationCode
    meaning: str
    direction_hint: str


class AnnotationDictionariesResponse(BaseModel):
    eras: list[AnnotationEraEntry]
    historical_events: list[AnnotationHistoricalEventEntry]
    relation_codes: list[AnnotationRelationCodeEntry]


AdjudicationDecision = Literal["a", "b", "manual"]
AdjudicationOperation = Literal["replace", "add", "remove"]


class AdjudicationDifference(BaseModel):
    id: str
    category: str
    label: str
    path: str
    operation: AdjudicationOperation
    value_a: Any = None
    value_b: Any = None


class AdjudicationResolution(BaseModel):
    difference_id: str = Field(min_length=1, max_length=64)
    decision: AdjudicationDecision
    manual_value: Any = None
    note: str = Field(default="", max_length=500)


class ExtractionAdjudicationUpdateRequest(BaseModel):
    revision: int = Field(ge=0)
    resolutions: list[AdjudicationResolution] = Field(default_factory=list, max_length=2000)
    label_override: ExtractionAnnotationLabel | None = None
    change_reason: str = Field(default="", max_length=2000)


class AdjudicationSubmissionResponse(BaseModel):
    id: int
    slot_no: int
    revision: int
    submitted_at: datetime
    label: ExtractionAnnotationLabel


class ExtractionAdjudicationDraftResponse(BaseModel):
    id: int | None
    revision: int
    resolutions: list[AdjudicationResolution]
    gold_label: ExtractionAnnotationLabel
    change_reason: str
    updated_at: datetime | None


class ExtractionGoldVersionResponse(BaseModel):
    id: int
    task_id: int
    version: int
    label: ExtractionAnnotationLabel
    source_submission_ids: list[int]
    reviewer_id: int
    change_reason: str
    locked_at: datetime


class ExtractionAdjudicationSummaryResponse(BaseModel):
    task_id: int
    passage_id: int
    passage_title: str
    task_status: str
    submitted_count: int
    difference_count: int
    resolved_count: int
    latest_gold_version: int | None
    updated_at: datetime


class ExtractionAdjudicationDetailResponse(BaseModel):
    task_id: int
    task_status: str
    spec_version: str
    context_sha256: str
    passage: AnnotationPassageResponse
    submissions: list[AdjudicationSubmissionResponse]
    differences: list[AdjudicationDifference]
    draft: ExtractionAdjudicationDraftResponse
    latest_gold: ExtractionGoldVersionResponse | None
