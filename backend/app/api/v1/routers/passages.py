"""古籍文章接口。

设计要点：
- /upload 与 /manual 立即返回 PassageResponse(workflow_status='pending')，
  workflow 真正执行放进 BackgroundTasks，避免前端长时间挂起。
- 前端拿到 pending 列表后立刻进任务监控页，polling /runs 看 5 个 step 进度。
"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision, StepExecutionResult
from app.core.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models.passage import Passage
from app.models.user import User
from app.schemas.passage import (
    PassageExecutionRunResponse,
    PassageManualCreateRequest,
    PassageResponse,
    PassageSummaryResponse,
)
from app.services.passage_service import MARKITDOWN_SUPPORTED_SUFFIXES, PassageService

router = APIRouter(prefix="/passages", tags=["古籍文章"])


def _trigger_workflow(
    db: Session, user: User, passage: Passage, trigger_type: str
) -> StepExecutionResult:
    """同步执行一次 workflow，返回 StepExecutionResult。"""

    context = ExecutionContext(
        user={"id": user.id, "role": user.role, "email": user.email},
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
    return Executor().execute(
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


def _resolve_passage_status(result: StepExecutionResult) -> str:
    if not result.success:
        return "failed"
    return result.output.get("status") or "success"


def _run_workflow_background(user_id: int, passage_id: int, trigger_type: str) -> None:
    """BackgroundTasks 入口：用独立 SessionLocal 跑完 workflow 并回写 passage 状态。

    路由注入的 db 在 response 返回后即被 close，因此这里必须新开 session。
    """

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        passage = db.get(Passage, passage_id)
        if user is None or passage is None:
            return
        passage.workflow_status = "running"
        db.add(passage)
        db.commit()
        db.refresh(passage)

        result = _trigger_workflow(db, user, passage, trigger_type=trigger_type)
        new_status = _resolve_passage_status(result)

        # workflow 跑完后再开一次 fresh query，避免 session 内对象被 detach
        fresh = db.get(Passage, passage_id)
        if fresh is not None:
            fresh.workflow_status = new_status
            db.add(fresh)
            db.commit()
    finally:
        db.close()


@router.post("/manual", response_model=PassageResponse)
def create_manual_passage(
    payload: PassageManualCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PassageResponse:
    """手工录入古籍文章；立即返回 pending，workflow 后台跑。"""

    service = PassageService(db)
    passage = service.create_passage(
        user=current_user,
        title=payload.title,
        context=payload.context,
        source_type="manual_input",
    )
    background_tasks.add_task(
        _run_workflow_background, current_user.id, passage.doc_id, "passage_manual_input"
    )
    return PassageResponse.model_validate(passage)


@router.post("/upload", response_model=list[PassageResponse])
async def upload_passages(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PassageResponse]:
    """上传一个或多个古籍文件；立即返回 pending 列表，workflow 后台跑。"""

    service = PassageService(db)
    passages: list[PassageResponse] = []

    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in MARKITDOWN_SUPPORTED_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的文件类型：{file.filename}",
            )

        file_bytes = await file.read()
        try:
            title, context = service.extract_text_via_markitdown(
                file.filename or f"untitled{suffix}", file_bytes
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件解析失败：{file.filename}",
            ) from exc

        passage = service.create_passage(
            user=current_user,
            title=title,
            context=context,
            source_type="upload",
            file_name=file.filename,
        )
        background_tasks.add_task(
            _run_workflow_background, current_user.id, passage.doc_id, "passage_upload"
        )
        passages.append(PassageResponse.model_validate(passage))

    return passages


@router.get("", response_model=list[PassageSummaryResponse])
def list_passages(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PassageSummaryResponse]:
    service = PassageService(db)
    return [PassageSummaryResponse.model_validate(item) for item in service.list_passages()]


@router.get("/{doc_id}", response_model=PassageResponse)
def get_passage(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PassageResponse:
    service = PassageService(db)
    try:
        passage = service.get_passage(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PassageResponse.model_validate(passage)


@router.get("/{doc_id}/runs", response_model=list[PassageExecutionRunResponse])
def list_passage_runs(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PassageExecutionRunResponse]:
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
