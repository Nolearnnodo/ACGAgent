"""功能 A 知识抽取人工标注接口。"""

from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin_user, get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.extraction_annotation import (
    AnnotationDictionariesResponse,
    AnnotationEraEntry,
    AnnotationHistoricalEventEntry,
    AnnotationRelationCodeEntry,
    ExtractionAdjudicationDetailResponse,
    ExtractionAdjudicationSummaryResponse,
    ExtractionAdjudicationUpdateRequest,
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


router = APIRouter(prefix="/annotations/extraction", tags=["功能 A 人工标注"])
T = TypeVar("T")


def _run_service(action: Callable[[], T]) -> T:
    try:
        return action()
    except ExtractionAnnotationServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
