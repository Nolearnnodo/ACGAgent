"""对话相关数据模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    """创建会话请求。"""

    title: str = Field(default="新对话", min_length=1, max_length=255)


class ConversationUpdateRequest(BaseModel):
    """更新会话请求。"""

    title: str = Field(min_length=1, max_length=255)


class MessageCreateRequest(BaseModel):
    """发送消息请求。"""

    content: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    """消息响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    sequence: int
    created_at: datetime


class ConversationResponse(BaseModel):
    """会话响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    """会话详情响应。"""

    messages: list[MessageResponse]


# ── 消息推理过程追踪 ──────────────────────────────────────────────


class PlannerDecisionResponse(BaseModel):
    intent: str
    decision_type: str
    target_skill_code: str
    reason: str


class ExecutionStepResponse(BaseModel):
    step_no: int
    skill_code: str
    status: str
    output_preview: dict[str, Any] | None = None


class LLMCallSummaryResponse(BaseModel):
    skill_code: str
    call_purpose: str
    provider: str
    model: str
    total_tokens: int
    latency_ms: int
    status: str
    response_text: str


class ToolCallSummaryResponse(BaseModel):
    skill_code: str
    tool_name: str
    input_preview: dict[str, Any] | None = None
    output_preview: dict[str, Any] | None = None
    latency_ms: int
    status: str
    error_message: str = ""


class TraceSummaryResponse(BaseModel):
    llm_call_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    estimated_total_cost: float = 0.0
    currency: str = "CNY"
    total_latency_ms: int = 0


class MessageTraceResponse(BaseModel):
    planner: PlannerDecisionResponse | None = None
    execution_status: str | None = None
    execution_started_at: datetime | None = None
    execution_finished_at: datetime | None = None
    steps: list[ExecutionStepResponse] = []
    llm_calls: list[LLMCallSummaryResponse] = []
    tool_calls: list[ToolCallSummaryResponse] = []
    summary: TraceSummaryResponse | None = None
