"""人工审核路由：同名人物身份裁定。"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import get_current_user
from app.graph.repository import GraphRepository
from app.models.user import User
from app.schemas.review import (
    AdjudicateRequest,
    AdjudicateResponse,
    IdentityEvidenceResponse,
    IdentityReviewItem,
    IdentityReviewListResponse,
)

router = APIRouter(prefix="/review", tags=["人工审核"])


@router.get("/identity-pending", response_model=IdentityReviewListResponse)
def list_pending_reviews(current_user: User = Depends(get_current_user)):
    """列出所有待人工审核的"可能同人"对。"""

    repo = GraphRepository()
    result = repo.list_pending_identity_reviews()
    records = result.get("records", [])
    items = [IdentityReviewItem(**r) for r in records]
    return IdentityReviewListResponse(pending_count=len(items), items=items)


@router.get(
    "/identity/{source_id}/{target_id}/evidence",
    response_model=IdentityEvidenceResponse,
)
def get_review_evidence(
    source_id: int,
    target_id: int,
    current_user: User = Depends(get_current_user),
):
    """获取两个候选同人的证据包及图谱元素。"""

    repo = GraphRepository()
    source_evidence = repo.get_person_evidence_bundle(person_id=source_id, max_hops=3)
    target_evidence = repo.get_person_evidence_bundle(person_id=target_id, max_hops=3)

    graph_elements = GraphRepository.build_graph_elements_from_evidence_bundles(
        [source_evidence, target_evidence]
    )

    # 查询 review 关系详情
    review_rel = repo.run_read_query(
        cypher="""
        MATCH (s:Person_Nodes {person_id: $source})-[r:可能同人]->(t:Person_Nodes {person_id: $target})
        RETURN r.confidence AS confidence, r.reason AS reason, r.evidence AS evidence
        """,
        parameters={"source": source_id, "target": target_id},
    )
    review_records = review_rel.get("records", [])
    review_record = review_records[0] if review_records else None

    return IdentityEvidenceResponse(
        source_evidence=source_evidence,
        target_evidence=target_evidence,
        graph_elements=graph_elements,
        review_relation=review_record,
    )


@router.post(
    "/identity/{source_id}/{target_id}/adjudicate",
    response_model=AdjudicateResponse,
)
def adjudicate_identity(
    source_id: int,
    target_id: int,
    payload: AdjudicateRequest,
    current_user: User = Depends(get_current_user),
):
    """执行身份裁定（仅管理员可用）：merge 合并或 keep_separate 保持独立。"""

    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可执行裁定操作。",
        )
    if payload.decision not in ("merge", "keep_separate"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision 必须为 merge 或 keep_separate。",
        )

    repo = GraphRepository()
    result = repo.adjudicate_identity(source_id, target_id, payload.decision)
    return AdjudicateResponse(
        status=result.get("status", "success"),
        action=payload.decision,
        detail=result,
    )
