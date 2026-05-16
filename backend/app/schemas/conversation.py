"""对话相关数据模型。"""

from datetime import datetime

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
