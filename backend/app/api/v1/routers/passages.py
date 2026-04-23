"""古籍文章接口。"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision
from app.core.deps import get_current_admin_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.passage import (
    PassageExecutionRunResponse,
    PassageManualCreateRequest,
    PassageResponse,
    PassageSummaryResponse,
)
from app.services.passage_service import PassageService

router = APIRouter(prefix="/passages", tags=["古籍文章"])


def trigger_passage_workflow(db: Session, current_admin: User, passage, trigger_type: str) -> None:
    """为 Passage 触发固定 workflow。"""

    context = ExecutionContext(
        user={"id": current_admin.id, "role": current_admin.role, "email": current_admin.email},
        conversation={},
        metadata={
            "trigger_type": trigger_type,
            "passage_id": passage.doc_id,
            "passage": {
                "doc_id": passage.doc_id,
                "title": passage.title,
                "context": passage.context,
                "source_type": passage.source_type,
            },
        },
    )
    executor = Executor()
    executor.execute(
        db=db,
        context=context,
        planner_decision=PlannerDecision(
            intent="passage_ingestion",
            decision_type="workflow",
            target_skill_code="passage_ingestion_workflow",
            reason="古籍上传或手工录入后触发固定处理流程。",
            arguments={"doc_id": passage.doc_id, "title": passage.title},
        ),
        planner_record_id=None,
        trigger_message_id=None,
    )


@router.post("/manual", response_model=PassageResponse)
def create_manual_passage(
    payload: PassageManualCreateRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> PassageResponse:
    """手工录入古籍文章。"""

    service = PassageService(db)
    passage = service.create_passage(
        user=current_admin,
        title=payload.title,
        context=payload.context,
        source_type="manual_input",
    )
    trigger_passage_workflow(db, current_admin, passage, trigger_type="passage_manual_input")
    passage = service.update_workflow_status(passage, "success")
    return PassageResponse.model_validate(passage)


@router.post("/upload", response_model=list[PassageResponse])
async def upload_passages(
    files: list[UploadFile] = File(...),
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[PassageResponse]:
    """上传一个或多个古籍文件。"""

    service = PassageService(db)
    passages = []

    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".md", ".docx"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的文件类型：{file.filename}",
            )

        file_bytes = await file.read()
        if suffix == ".md":
            title, context = service.extract_text_from_md(file.filename or "untitled.md", file_bytes)
        else:
            try:
                title, context = service.extract_text_from_docx(file.filename or "untitled.docx", file_bytes)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"docx 解析失败：{file.filename}",
                ) from exc

        passage = service.create_passage(
            user=current_admin,
            title=title,
            context=context,
            source_type="upload",
            file_name=file.filename,
        )
        trigger_passage_workflow(db, current_admin, passage, trigger_type="passage_upload")
        passage = service.update_workflow_status(passage, "success")
        passages.append(PassageResponse.model_validate(passage))

    return passages


@router.get("", response_model=list[PassageSummaryResponse])
def list_passages(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[PassageSummaryResponse]:
    """列出全部古籍文章摘要。"""

    service = PassageService(db)
    return [PassageSummaryResponse.model_validate(item) for item in service.list_passages()]


@router.get("/{doc_id}", response_model=PassageResponse)
def get_passage(
    doc_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> PassageResponse:
    """获取文章详情。"""

    service = PassageService(db)
    try:
        passage = service.get_passage(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PassageResponse.model_validate(passage)


@router.get("/{doc_id}/runs", response_model=list[PassageExecutionRunResponse])
def list_passage_runs(
    doc_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[PassageExecutionRunResponse]:
    """获取文章关联的任务运行记录。"""

    service = PassageService(db)
    try:
        service.get_passage(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    results = []
    for run, steps in service.list_passage_runs(doc_id):
        results.append(
            PassageExecutionRunResponse(
                id=run.id,
                trigger_type=run.trigger_type,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                steps=steps,
            )
        )
    return results
