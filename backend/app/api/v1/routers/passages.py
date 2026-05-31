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
    PassageLLMCallUsageResponse,
    PassageManualCreateRequest,
    PassageResponse,
    PassageSummaryResponse,
    PassageTokenUsageResponse,
    PassageTraceSummaryResponse,
    PassageUploadResponse,
    PassageUsageOverviewItem,
    PassageUsageOverviewResponse,
)
from app.services.passage_service import MARKITDOWN_SUPPORTED_SUFFIXES, PassageService

router = APIRouter(prefix="/passages", tags=["古籍文章"])


def _build_skipped_upload_response(
    passage: Passage,
    skip_reason: str,
) -> PassageUploadResponse:
    return PassageUploadResponse.model_validate(passage).model_copy(
        update={
            "upload_status": "skipped_existing",
            "skip_reason": skip_reason,
        }
    )


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

        try:
            result = _trigger_workflow(db, user, passage, trigger_type=trigger_type)
            new_status = _resolve_passage_status(result)
        except Exception:
            db.rollback()
            failed = db.get(Passage, passage_id)
            if failed is not None:
                failed.workflow_status = "failed"
                db.add(failed)
                db.commit()
            raise

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
    content_hash = service.compute_content_hash(payload.context)
    existing = service.find_duplicate_passage(payload.context, content_hash)
    if existing is not None:
        return PassageResponse.model_validate(existing)

    passage = service.create_passage(
        user=current_user,
        title=payload.title,
        context=payload.context,
        source_type="manual_input",
        content_hash=content_hash,
    )
    background_tasks.add_task(
        _run_workflow_background, current_user.id, passage.doc_id, "passage_manual_input"
    )
    return PassageResponse.model_validate(passage)


@router.post("/upload", response_model=list[PassageUploadResponse])
async def upload_passages(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PassageUploadResponse]:
    """上传一个或多个古籍文件；立即返回 pending 列表，workflow 后台跑。"""

    service = PassageService(db)
    prepared_uploads: list[dict[str, str | None]] = []

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

        prepared_uploads.append(
            {
                "file_name": file.filename,
                "title": title,
                "context": context,
                "content_hash": service.compute_content_hash(context),
            }
        )

    responses: list[PassageUploadResponse | None] = [None] * len(prepared_uploads)
    created_passage_ids: list[int] = []
    created_by_hash: dict[str, Passage] = {}

    try:
        for index, prepared in enumerate(prepared_uploads):
            context = str(prepared["context"] or "")
            content_hash = str(prepared["content_hash"] or "")

            duplicate_in_batch = created_by_hash.get(content_hash)
            if duplicate_in_batch is not None:
                responses[index] = _build_skipped_upload_response(
                    duplicate_in_batch,
                    f"本次上传中已创建文章 doc_id={duplicate_in_batch.doc_id}，已跳过重复入队。",
                )
                continue

            existing = service.find_duplicate_passage(context, content_hash)
            if existing is not None:
                responses[index] = _build_skipped_upload_response(
                    existing,
                    f"已存在文章 doc_id={existing.doc_id}，状态={existing.workflow_status}，已跳过入队。",
                )
                continue

            passage = service.create_passage(
                user=current_user,
                title=str(prepared["title"] or ""),
                context=context,
                source_type="upload",
                file_name=prepared["file_name"],
                content_hash=content_hash,
                commit=False,
            )
            created_passage_ids.append(passage.doc_id)
            created_by_hash[content_hash] = passage
            responses[index] = PassageUploadResponse.model_validate(passage)

        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量上传写入失败，已回滚本次新增文章。",
        ) from exc

    for passage_id in created_passage_ids:
        background_tasks.add_task(
            _run_workflow_background, current_user.id, passage_id, "passage_upload"
        )

    return [response for response in responses if response is not None]


@router.get("", response_model=list[PassageSummaryResponse])
def list_passages(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PassageSummaryResponse]:
    service = PassageService(db)
    return [PassageSummaryResponse.model_validate(item) for item in service.list_passages()]


@router.get("/token-usage-overview", response_model=PassageUsageOverviewResponse)
def get_all_passages_token_usage_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PassageUsageOverviewResponse:
    """返回所有古籍的资源追踪汇总数据。"""

    service = PassageService(db)
    rows = service.get_passage_usage_overview_rows()

    items: list[PassageUsageOverviewItem] = []
    total_llm_calls = 0
    total_tokens = 0
    total_cost = 0.0

    for row in rows:
        item = PassageUsageOverviewItem(
            doc_id=row.doc_id,
            title=row.title,
            workflow_status=row.workflow_status,
            llm_call_count=int(row.llm_call_count),
            total_tokens=int(row.total_tokens),
            prompt_cache_hit_tokens=row.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=row.prompt_cache_miss_tokens,
            cache_hit_ratio=row.cache_hit_ratio,
            estimated_total_cost=float(row.estimated_total_cost),
            currency=row.currency,
        )
        items.append(item)
        total_llm_calls += item.llm_call_count
        total_tokens += item.total_tokens
        total_cost += item.estimated_total_cost

    return PassageUsageOverviewResponse(
        items=items,
        total_llm_calls=total_llm_calls,
        total_tokens=total_tokens,
        total_cost=total_cost,
        currency="CNY",
    )


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


@router.get("/{doc_id}/token-usage", response_model=PassageTokenUsageResponse)
def get_passage_token_usage(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PassageTokenUsageResponse:
    service = PassageService(db)
    try:
        service.get_passage(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    execution_run_id, summary, calls = service.get_latest_passage_trace(doc_id)

    return PassageTokenUsageResponse(
        doc_id=doc_id,
        execution_run_id=execution_run_id,
        summary=(
            PassageTraceSummaryResponse.model_validate(summary, from_attributes=True)
            if summary is not None
            else None
        ),
        llm_calls=[
            PassageLLMCallUsageResponse.model_validate(call, from_attributes=True)
            for call in calls
        ],
    )
