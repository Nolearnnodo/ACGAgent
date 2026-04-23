"""通用响应模型。"""

from pydantic import BaseModel


class MessageResponse(BaseModel):
    """简单文本响应。"""

    message: str
