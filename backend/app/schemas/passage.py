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
