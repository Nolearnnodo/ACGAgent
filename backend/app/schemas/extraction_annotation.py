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


class ExtractionTaskAssignmentResponse(BaseModel):
    submission_id: int
    slot_no: int
    annotator_id: int
    annotator_email: str
    state: str
    submitted_at: datetime | None
    updated_at: datetime


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
    assignments: list[ExtractionTaskAssignmentResponse] = Field(default_factory=list)


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


class AIAnnotationConfigRequest(BaseModel):
    """单次 AI 标注调用的配置；API Key 不会写入数据库。"""

    provider: Literal["", "mock", "deepseek", "openai_compatible"] = ""
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    extra_instruction: str = Field(default="", max_length=20000)


class AIAnnotationPromptOverrides(BaseModel):
    """仅本次调用使用的提示词覆盖值，不写入配置。"""

    system_prompt: str = Field(min_length=1, max_length=100_000)
    user_prompt: str = Field(min_length=1, max_length=10_000_000)


class AIAnnotationGenerateRequest(BaseModel):
    task_id: int = Field(gt=0)
    config: AIAnnotationConfigRequest = Field(default_factory=AIAnnotationConfigRequest)
    prompt_overrides: AIAnnotationPromptOverrides | None = None
    client_started_at: datetime | None = None


class AIAnnotationPromptRequest(BaseModel):
    task_id: int = Field(gt=0)
    config: AIAnnotationConfigRequest = Field(default_factory=AIAnnotationConfigRequest)


class AIAnnotationPromptResponse(BaseModel):
    task_id: int
    prompt_version: str
    system_prompt: str
    user_prompt: str


class AIAnnotationTaskContextResponse(BaseModel):
    task_id: int
    context_sha256: str
    spec_version: str
    status: str
    passage: AnnotationPassageResponse


class AIAnnotationValidateRequest(BaseModel):
    task_id: int = Field(gt=0)
    job_id: int | None = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=10_000_000)


class AIAnnotationRepairRequest(BaseModel):
    """根据当前结果触发分片修复/重新抽取，不让模型重写完整 JSON。"""

    task_id: int = Field(gt=0)
    job_id: int | None = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=10_000_000)
    config: AIAnnotationConfigRequest = Field(default_factory=AIAnnotationConfigRequest)
    client_started_at: datetime | None = None


class AIAnnotationValidationIssue(BaseModel):
    path: str
    message: str
    code: str


class AIAnnotationExportMetadata(BaseModel):
    task_id: int
    spec_version: str
    context_sha256: str


class AIAnnotationExportPassage(BaseModel):
    doc_id: int
    title: str
    context: str
    context_sha256: str


class AIAnnotationExportDocument(BaseModel):
    """与独立盲标导入接口兼容的完整文件结构。"""

    metadata: AIAnnotationExportMetadata
    passage: AIAnnotationExportPassage
    label: ExtractionAnnotationLabel


AIAnnotationValidationStatus = Literal["valid", "invalid_format", "invalid_rules"]


class AIAnnotationTokenUsage(BaseModel):
    """一次生成或修复任务内全部 LLM 分片调用的累计用量。"""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    cache_metrics_supported: bool = False
    llm_call_count: int = Field(default=0, ge=0)


class AIAnnotationResultResponse(BaseModel):
    task_id: int
    passage_id: int
    passage_title: str
    spec_version: str
    context_sha256: str
    source: Literal["generated", "validated"]
    provider: str = ""
    model: str = ""
    raw_content: str
    document: AIAnnotationExportDocument | None = None
    label: ExtractionAnnotationLabel | None = None
    validation_status: AIAnnotationValidationStatus
    validation_issues: list[AIAnnotationValidationIssue] = Field(default_factory=list)
    elapsed_ms: int = Field(ge=0)
    # 分片调用诊断；旧任务结果没有这些字段时使用默认值，保持兼容。
    fragment_count: int = Field(default=0, ge=0)
    truncated_fragments: list[str] = Field(default_factory=list)
    fragment_warnings: list[str] = Field(default_factory=list)
    token_usage: AIAnnotationTokenUsage = Field(default_factory=AIAnnotationTokenUsage)


AIAnnotationJobStatus = Literal["queued", "running", "success", "failed"]
AIAnnotationJobOperation = Literal["generate", "repair"]


class AIAnnotationJobResponse(BaseModel):
    id: int
    task_id: int
    passage_id: int
    passage_title: str
    operation: AIAnnotationJobOperation = "generate"
    status: AIAnnotationJobStatus
    provider: str = ""
    model: str = ""
    error_message: str = ""
    client_started_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    submitted_at: datetime | None = None
    token_usage: AIAnnotationTokenUsage = Field(default_factory=AIAnnotationTokenUsage)
    first_round_validation_status: str = ""
    first_round_error_count: int = Field(default=0, ge=0)
    first_round_warning_count: int = Field(default=0, ge=0)
    first_round_truncated_count: int = Field(default=0, ge=0)
    final_difference_count: int = Field(default=0, ge=0)
    saved_result_source_job_id: int | None = None
    result: AIAnnotationResultResponse | None = None
    saved_result: AIAnnotationResultResponse | None = None


class AIAnnotationMetricError(BaseModel):
    code: str
    example_message: str = ""
    count: int = Field(ge=0)
    session_count: int = Field(ge=0)


class AIAnnotationMetricDifference(BaseModel):
    category: str
    label: str
    count: int = Field(ge=0)


class AIAnnotationDailyMetric(BaseModel):
    date: str
    session_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    first_round_error_count: int = Field(ge=0)


class AIAnnotationMetricsSummary(BaseModel):
    session_count: int = Field(ge=0)
    completed_first_round_count: int = Field(ge=0)
    failed_first_round_count: int = Field(ge=0)
    first_pass_valid_count: int = Field(ge=0)
    first_pass_valid_rate: float = Field(ge=0, le=1)
    submitted_count: int = Field(ge=0)
    submission_rate: float = Field(ge=0, le=1)
    average_first_round_ms: int = Field(ge=0)
    average_end_to_end_ms: int = Field(ge=0)
    total_prompt_tokens: int = Field(ge=0)
    total_completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cache_hit_tokens: int = Field(ge=0)
    total_cache_miss_tokens: int = Field(ge=0)
    total_llm_call_count: int = Field(ge=0)
    total_repair_rounds: int = Field(ge=0)
    total_first_round_errors: int = Field(ge=0)
    total_final_differences: int = Field(ge=0)


class AIAnnotationMetricsRecord(BaseModel):
    session_id: int
    task_id: int
    passage_id: int
    passage_title: str
    requested_by: int
    requested_by_email: str
    provider: str
    model: str
    status: str
    first_round_validation_status: str
    client_started_at: datetime
    queued_at: datetime
    started_at: datetime | None = None
    first_round_finished_at: datetime | None = None
    submitted_at: datetime | None = None
    queue_wait_ms: int | None = Field(default=None, ge=0)
    first_round_elapsed_ms: int | None = Field(default=None, ge=0)
    end_to_end_ms: int | None = Field(default=None, ge=0)
    token_usage: AIAnnotationTokenUsage = Field(default_factory=AIAnnotationTokenUsage)
    repair_rounds: int = Field(default=0, ge=0)
    first_round_errors: list[AIAnnotationValidationIssue] = Field(default_factory=list)
    first_round_warning_count: int = Field(default=0, ge=0)
    first_round_truncated_count: int = Field(default=0, ge=0)
    final_difference_count: int = Field(default=0, ge=0)
    final_difference_categories: dict[str, int] = Field(default_factory=dict)
    submission_id: int | None = None


class AIAnnotationMetricsResponse(BaseModel):
    generated_at: datetime
    days: int = Field(ge=0)
    summary: AIAnnotationMetricsSummary
    validation_errors: list[AIAnnotationMetricError] = Field(default_factory=list)
    final_differences: list[AIAnnotationMetricDifference] = Field(default_factory=list)
    daily: list[AIAnnotationDailyMetric] = Field(default_factory=list)
    records: list[AIAnnotationMetricsRecord] = Field(default_factory=list)


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
