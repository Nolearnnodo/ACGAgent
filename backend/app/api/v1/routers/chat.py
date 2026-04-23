"""对话接口。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageCreateRequest,
)
from app.schemas.common import MessageResponse
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/chat", tags=["对话"])


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationResponse]:
    """列出当前用户的全部对话。"""

    service = ConversationService(db)
    conversations = service.list_conversations(current_user)
    return [ConversationResponse.model_validate(item) for item in conversations]


@router.post("/conversations", response_model=ConversationResponse)
def create_conversation(
    payload: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    """创建新对话。"""

    service = ConversationService(db)
    conversation = service.create_conversation(current_user, payload.title)
    return ConversationResponse.model_validate(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation_detail(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetailResponse:
    """获取会话详情。"""

    service = ConversationService(db)
    try:
        conversation = service.get_conversation_detail(current_user, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConversationDetailResponse.model_validate(conversation)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
def rename_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    """重命名会话。"""

    service = ConversationService(db)
    try:
        conversation = service.rename_conversation(current_user, conversation_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ConversationResponse.model_validate(conversation)


@router.delete("/conversations/{conversation_id}", response_model=MessageResponse)
def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """删除会话。"""

    service = ConversationService(db)
    try:
        service.delete_conversation(current_user, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return MessageResponse(message="会话已删除。")


@router.post("/conversations/{conversation_id}/messages", response_model=ConversationDetailResponse)
def send_message(
    conversation_id: int,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetailResponse:
    """发送用户消息并触发 Agent 执行。"""

    service = ConversationService(db)
    try:
        conversation, _, _ = service.add_user_message_and_execute(current_user, conversation_id, payload.content)
        conversation = service.get_conversation_detail(current_user, conversation.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ConversationDetailResponse.model_validate(conversation)
