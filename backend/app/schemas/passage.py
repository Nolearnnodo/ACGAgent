"""古籍文章相关数据模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PassageManualCreateRequest(BaseModel):
    """手工录入古籍请求。"""

    title: str = Field(min_length=1, max_length=255)
    context: str = Field(min_length=1, max_length=200000)


class PassageResponse(BaseModel):
    """古籍文章响应。"""

    model_config = ConfigDict(from_attributes=True)

    doc_id: int
    title: str
    context: str
    source_type: str
    file_name: str | None
    created_by: int
    workflow_status: str
    created_at: datetime
    updated_at: datetime


class PassageUploadResponse(PassageResponse):
    """上传单篇文件后的处理结果。"""

    upload_status: str = "queued"
    skip_reason: str | None = None


class PassageSummaryResponse(BaseModel):
    """古籍文章列表摘要。"""

    model_config = ConfigDict(from_attributes=True)

    doc_id: int
    title: str
    source_type: str
    workflow_status: str
    updated_at: datetime


class ExecutionStepRunResponse(BaseModel):
    """执行步骤响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    step_no: int
    skill_code: str
    status: str
    input_json: str
    output_json: str
    error_message: str
    created_at: datetime


class PassageExecutionRunResponse(BaseModel):
    """与 Passage 关联的执行记录响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    steps: list[ExecutionStepRunResponse]


class PassageLLMCallUsageResponse(BaseModel):
    """Single LLM call usage row for a passage workflow."""

    id: int
    execution_run_id: int | None
    execution_step_run_id: int | None
    passage_id: int | None
    skill_code: str
    provider: str
    model: str
    status: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    cache_hit_ratio: float
    cache_metrics_supported: bool
    estimated_total_cost: float
    currency: str
    error_message: str
    created_at: datetime


class PassageTraceSummaryResponse(BaseModel):
    """Aggregated token, cache, and cost summary for one execution run."""

    execution_run_id: int
    llm_call_count: int
    tool_call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    cache_hit_ratio: float
    estimated_input_cost: float
    estimated_output_cost: float
    estimated_total_cost: float
    currency: str
    total_latency_ms: int
    failed_call_count: int
    updated_at: datetime


class PassageTokenUsageResponse(BaseModel):
    """Latest passage workflow observability payload."""

    doc_id: int
    execution_run_id: int | None
    summary: PassageTraceSummaryResponse | None
    llm_calls: list[PassageLLMCallUsageResponse]


class PassageUsageOverviewItem(BaseModel):
    """Per-passage aggregated resource tracking."""

    doc_id: int
    title: str
    workflow_status: str
    llm_call_count: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    cache_hit_ratio: float
    estimated_total_cost: float
    currency: str


class PassageUsageOverviewResponse(BaseModel):
    """All passages resource tracking overview."""

    items: list[PassageUsageOverviewItem]
    total_llm_calls: int
    total_tokens: int
    total_cost: float
    currency: str
