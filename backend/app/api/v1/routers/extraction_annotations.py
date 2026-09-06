"""功能 A 知识抽取人工标注接口。"""

from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin_user, get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.extraction_annotation import (
    AIAnnotationGenerateRequest,
    AIAnnotationJobResponse,
    AIAnnotationMetricsResponse,
    AIAnnotationPromptRequest,
    AIAnnotationPromptResponse,
    AIAnnotationRepairRequest,
    AIAnnotationResultResponse,
    AIAnnotationTaskContextResponse,
    AIAnnotationValidateRequest,
    AnnotationDictionariesResponse,
    AnnotationEraEntry,
    AnnotationHistoricalEventEntry,
    AnnotationRelationCodeEntry,
    ExtractionAdjudicationDetailResponse,
    ExtractionAdjudicationSummaryResponse,
    ExtractionAdjudicationUpdateRequest,
    ExtractionGoldVersionResponse,
    ExtractionDraftUpdateRequest,
    ExtractionSubmitRequest,
    ExtractionTaskCreateRequest,
    ExtractionTaskDetailResponse,
    ExtractionTaskSummaryResponse,
)
from app.services.dictionary_service import (
    get_era_entries,
    get_historical_events,
    get_relation_codes,
)
from app.services.extraction_adjudication_service import ExtractionAdjudicationService
from app.services.extraction_annotation_service import (
    ExtractionAnnotationService,
    ExtractionAnnotationServiceError,
)
from app.services.extraction_ai_annotation_service import ExtractionAIAnnotationService
from app.services.extraction_ai_annotation_job_service import ExtractionAIAnnotationJobService
from app.services.extraction_ai_annotation_metrics_service import (
    ExtractionAIAnnotationMetricsService,
)


router = APIRouter(prefix="/annotations/extraction", tags=["功能 A 人工标注"])
T = TypeVar("T")


def _run_service(action: Callable[[], T]) -> T:
    try:
        return action()
    except ExtractionAnnotationServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/ai/generate", response_model=AIAnnotationJobResponse, status_code=202)
def generate_ai_extraction_annotation(
    payload: AIAnnotationGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationJobResponse:
    """将一次 AI 标注请求写入持久化队列并立即返回任务状态。"""

    return _run_service(
        lambda: ExtractionAIAnnotationJobService(db).enqueue(payload, current_user)
    )


@router.post("/ai/jobs", response_model=AIAnnotationJobResponse, status_code=202)
def enqueue_ai_extraction_annotation(
    payload: AIAnnotationGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationJobResponse:
    """AI 标注队列的显式入队入口。"""

    return _run_service(
        lambda: ExtractionAIAnnotationJobService(db).enqueue(payload, current_user)
    )


@router.get("/ai/jobs", response_model=list[AIAnnotationJobResponse])
def list_ai_extraction_annotation_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AIAnnotationJobResponse]:
    """返回当前用户每个篇目的最新 AI 标注任务状态。"""

    return ExtractionAIAnnotationJobService(db).list_latest_jobs(current_user)


@router.get("/ai/jobs/{job_id}", response_model=AIAnnotationJobResponse)
def get_ai_extraction_annotation_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationJobResponse:
    """返回一条持久化 AI 标注任务及其结果或错误。"""

    return _run_service(lambda: ExtractionAIAnnotationJobService(db).get_job(job_id, current_user))


@router.get("/ai/tasks/{task_id}", response_model=AIAnnotationTaskContextResponse)
def get_ai_extraction_task_context(
    task_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationTaskContextResponse:
    """提供 AI 工作台预览篇目所需的正文，不会自动领取盲标槽位。"""

    return _run_service(lambda: ExtractionAIAnnotationService(db).get_task_context(task_id))


@router.post("/ai/prompt", response_model=AIAnnotationPromptResponse)
def preview_ai_extraction_annotation_prompt(
    payload: AIAnnotationPromptRequest,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationPromptResponse:
    """返回当前篇目实际发送给模型的系统提示词和篇目载荷。"""

    return _run_service(lambda: ExtractionAIAnnotationService(db).preview_prompt(payload))


@router.post("/ai/validate", response_model=AIAnnotationResultResponse)
def validate_ai_extraction_annotation(
    payload: AIAnnotationValidateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationResultResponse:
    """校验并保存页面编辑后的 JSON，供当前篇目后续恢复。"""

    return _run_service(
        lambda: ExtractionAIAnnotationService(db).validate(
            payload.task_id,
            payload.content,
            requested_by=current_user.id,
            job_id=payload.job_id,
        )
    )


@router.post("/ai/repair", response_model=AIAnnotationJobResponse, status_code=202)
def repair_ai_extraction_annotation(
    payload: AIAnnotationRepairRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIAnnotationJobResponse:
    """把当前页面 JSON 加入 API 修复问题队列并保存结果。"""

    return _run_service(
        lambda: ExtractionAIAnnotationJobService(db).enqueue_repair(payload, current_user)
    )


@router.get("/ai/metrics", response_model=AIAnnotationMetricsResponse)
def get_ai_extraction_annotation_metrics(
    days: int = Query(default=30, ge=0, le=3650),
    limit: int = Query(default=500, ge=1, le=2000),
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AIAnnotationMetricsResponse:
    """返回 AI 标注首轮质量、Token、修复与最终提交耗时统计。"""

    return ExtractionAIAnnotationMetricsService(db).get_metrics(days=days, limit=limit)


@router.get("/tasks", response_model=list[ExtractionTaskSummaryResponse])
def list_extraction_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ExtractionTaskSummaryResponse]:
    """返回任务队列；普通用户只看自己的状态，管理员额外看到槽位标注员。"""

    return _run_service(lambda: ExtractionAnnotationService(db).list_tasks(current_user))


@router.post("/tasks", response_model=ExtractionTaskSummaryResponse)
def create_extraction_task(
    payload: ExtractionTaskCreateRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskSummaryResponse:
    """管理员从一篇现有古籍创建可领取的盲标任务。"""

    return _run_service(
        lambda: ExtractionAnnotationService(db).create_task(payload, current_user)
    )


@router.post("/tasks/{task_id}/claim", response_model=ExtractionTaskDetailResponse)
def claim_extraction_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskDetailResponse:
    return _run_service(
        lambda: ExtractionAnnotationService(db).claim_task(task_id, current_user)
    )


@router.delete(
    "/tasks/{task_id}/submissions/{submission_id}",
    response_model=ExtractionTaskSummaryResponse,
)
def release_extraction_draft(
    task_id: int,
    submission_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskSummaryResponse:
    """管理员释放误领的未提交草稿槽位；已提交结果不可释放。"""

    return _run_service(
        lambda: ExtractionAnnotationService(db).release_draft(
            task_id,
            submission_id,
            current_user,
        )
    )


@router.post(
    "/tasks/{task_id}/reset",
    response_model=ExtractionTaskSummaryResponse,
)
def reset_extraction_task(
    task_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskSummaryResponse:
    """管理员清空任务的所有标注与裁定数据，并保留任务重新开放。"""

    return _run_service(
        lambda: ExtractionAnnotationService(db).reset_task(task_id, current_user)
    )


@router.delete("/tasks/{task_id}", status_code=204)
def delete_extraction_task(
    task_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> Response:
    """管理员删除任务及其标注数据，不删除对应古籍正文。"""

    _run_service(lambda: ExtractionAnnotationService(db).delete_task(task_id, current_user))
    return Response(status_code=204)


@router.get("/tasks/{task_id}", response_model=ExtractionTaskDetailResponse)
def get_extraction_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskDetailResponse:
    """返回当前用户自己的盲标详情，不下发另一份标注或 AI 输出。"""

    return _run_service(
        lambda: ExtractionAnnotationService(db).get_task_detail(task_id, current_user)
    )


@router.put("/tasks/{task_id}/draft", response_model=ExtractionTaskDetailResponse)
def save_extraction_draft(
    task_id: int,
    payload: ExtractionDraftUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskDetailResponse:
    return _run_service(
        lambda: ExtractionAnnotationService(db).save_draft(
            task_id,
            current_user,
            payload.revision,
            payload.label,
        )
    )


@router.post("/tasks/{task_id}/import", response_model=ExtractionTaskDetailResponse)
async def import_extraction_draft(
    task_id: int,
    revision: int = Query(..., ge=0),
    allow_validation_issues: bool = Query(default=False),
    ai_job_id: int | None = Query(default=None, gt=0),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskDetailResponse:
    """把 YAML/JSON 标注结果导入当前用户的独立盲标草稿。"""

    filename = file.filename or ""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in {"yaml", "yml", "json"}:
        raise HTTPException(status_code=400, detail="仅支持 YAML、YML 或 JSON 标注结果文件。")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="标注结果文件不能超过 10 MB。")

    return _run_service(
        lambda: ExtractionAnnotationService(db).import_draft(
            task_id,
            current_user,
            revision,
            content,
            allow_validation_issues=allow_validation_issues,
            source_ai_job_id=ai_job_id,
        )
    )


@router.post("/tasks/{task_id}/submit", response_model=ExtractionTaskDetailResponse)
def submit_extraction_annotation(
    task_id: int,
    payload: ExtractionSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractionTaskDetailResponse:
    return _run_service(
        lambda: ExtractionAnnotationService(db).submit(
            task_id,
            current_user,
            payload.revision,
            payload.label,
        )
    )


@router.get("/dictionaries", response_model=AnnotationDictionariesResponse)
def get_annotation_dictionaries(
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=300, ge=1, le=2000),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnnotationDictionariesResponse:
    """返回盲标可使用的只读年号、历史事件和关系代码字典。"""

    keyword = query.strip().casefold()
    eras = [
        AnnotationEraEntry(
            era=item.era,
            dynasty=item.dynasty,
            start_year=item.start_year,
            end_year=item.end_year,
            simplified=item.simplified,
            traditional=item.traditional,
        )
        for item in get_era_entries(db)
        if not keyword
        or keyword in item.era.casefold()
        or keyword in item.dynasty.casefold()
        or keyword in item.simplified.casefold()
        or keyword in item.traditional.casefold()
    ][:limit]
    historical_events = [
        AnnotationHistoricalEventEntry(
            id=item.id,
            year_label=item.year_label,
            event_name=item.event_name,
            event_details=item.event_details,
            start_year=item.start_year,
            end_year=item.end_year,
        )
        for item in get_historical_events(db)
        if not keyword
        or keyword in item.event_name.casefold()
        or keyword in item.event_details.casefold()
        or keyword in item.year_label.casefold()
    ][:limit]
    relation_codes = [
        AnnotationRelationCodeEntry(
            code=item.code,
            meaning=item.meaning,
            direction_hint=item.direction_hint,
        )
        for item in get_relation_codes(db).values()
    ]
    return AnnotationDictionariesResponse(
        eras=eras,
        historical_events=historical_events,
        relation_codes=relation_codes,
    )


@router.get(
    "/adjudications",
    response_model=list[ExtractionAdjudicationSummaryResponse],
)
def list_extraction_adjudications(
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[ExtractionAdjudicationSummaryResponse]:
    return _run_service(lambda: ExtractionAdjudicationService(db).list_adjudications())


@router.get(
    "/adjudications/{task_id}",
    response_model=ExtractionAdjudicationDetailResponse,
)
def get_extraction_adjudication(
    task_id: int,
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionAdjudicationDetailResponse:
    return _run_service(lambda: ExtractionAdjudicationService(db).get_adjudication(task_id))


@router.get(
    "/adjudications/{task_id}/gold",
    response_model=ExtractionGoldVersionResponse,
)
def get_extraction_gold(
    task_id: int,
    version: int | None = Query(default=None, ge=1),
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionGoldVersionResponse:
    """返回指定任务的最新或指定版本金标，供下游批量抽取读取。"""

    return _run_service(lambda: ExtractionAdjudicationService(db).get_gold(task_id, version))


@router.post(
    "/adjudications/{task_id}/import",
    response_model=ExtractionGoldVersionResponse,
)
async def import_extraction_gold(
    task_id: int,
    file: UploadFile = File(...),
    change_reason: str = Query(default="", max_length=2000),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionGoldVersionResponse:
    """导入上次导出的 YAML/JSON 标注结果，保存为新的不可变金标版本。"""

    filename = file.filename or ""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in {"yaml", "yml", "json"}:
        raise HTTPException(status_code=400, detail="仅支持 YAML、YML 或 JSON 标注结果文件。")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="标注结果文件不能超过 10 MB。")

    return _run_service(
        lambda: ExtractionAdjudicationService(db).import_gold_yaml(
            task_id,
            content,
            current_user,
            source_name=filename,
            change_reason=change_reason,
        )
    )


@router.put(
    "/adjudications/{task_id}/draft",
    response_model=ExtractionAdjudicationDetailResponse,
)
def save_extraction_adjudication_draft(
    task_id: int,
    payload: ExtractionAdjudicationUpdateRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionAdjudicationDetailResponse:
    return _run_service(
        lambda: ExtractionAdjudicationService(db).save_draft(task_id, payload, current_user)
    )


@router.post(
    "/adjudications/{task_id}/lock",
    response_model=ExtractionAdjudicationDetailResponse,
)
def lock_extraction_gold(
    task_id: int,
    payload: ExtractionAdjudicationUpdateRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> ExtractionAdjudicationDetailResponse:
    return _run_service(
        lambda: ExtractionAdjudicationService(db).lock_gold(task_id, payload, current_user)
    )


@router.get("/adjudications/{task_id}/export")
def export_extraction_gold(
    task_id: int,
    version: int | None = Query(default=None, ge=1),
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> Response:
    filename, content = _run_service(
        lambda: ExtractionAdjudicationService(db).export_gold_yaml(task_id, version)
    )
    return Response(
        content=content,
        media_type="application/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
