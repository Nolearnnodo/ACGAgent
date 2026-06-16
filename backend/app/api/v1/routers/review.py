"""人工审核路由：同名人物身份裁定。"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.graph.repository import GraphRepository
from app.models.annotation import IdentityAnnotation
from app.models.execution import IdentityResolutionDecisionLog
from app.models.passage import Passage
from app.models.user import User
from app.schemas.review import (
    AdjudicateRequest,
    AdjudicateResponse,
    AnnotateRequest,
    AnnotateResponse,
    IdentityDecisionLog,
    IdentityEvidenceResponse,
    IdentityReviewItem,
    IdentityReviewListResponse,
    PassageText,
)

router = APIRouter(prefix="/review", tags=["人工审核"])


def _pair_key(source_id: int, target_id: int) -> tuple[int, int]:
    return (min(source_id, target_id), max(source_id, target_id))


@router.get("/identity-pending", response_model=IdentityReviewListResponse)
def list_pending_reviews(
    mode: str = Query("pending", pattern="^(pending|annotation)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出待审核的同名人物对。mode=annotation 时返回所有同名对。"""

    if mode != "annotation" and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可访问待审核列表。",
        )

    repo = GraphRepository()

    if mode == "annotation":
        result = repo.list_all_same_name_pairs()
    else:
        result = repo.list_pending_identity_reviews()

    records = result.get("records", [])

    annotated_pairs: set[tuple[int, int]] = set()
    if mode == "annotation" and records:
        all_annotations = (
            db.query(
                IdentityAnnotation.source_person_id,
                IdentityAnnotation.target_person_id,
            )
            .filter(IdentityAnnotation.annotator_id == current_user.id)
            .all()
        )
        annotated_pairs = {_pair_key(a[0], a[1]) for a in all_annotations}

    # 从 IdentityResolutionDecisionLog 取每对的首次可判定跳数+结论。
    # 全文终局裁定也存为 hop=5，按 (hop, used_full_text) 排序保证图证据裁定优先于全文裁定，
    # 仅当 5 跳图证据仍无法判定、靠全文才判出时，used_full_text 才为 True。
    first_decisions: dict[tuple[int, int], tuple[int, str, bool]] = {}
    if records:
        decision_rows = (
            db.query(IdentityResolutionDecisionLog)
            .filter(IdentityResolutionDecisionLog.decision.in_(("same", "different")))
            .order_by(
                IdentityResolutionDecisionLog.hop,
                IdentityResolutionDecisionLog.used_full_text,
            )
            .all()
        )
        for row in decision_rows:
            key = _pair_key(row.new_person_id, row.candidate_person_id)
            if key not in first_decisions:
                first_decisions[key] = (row.hop, row.decision, row.used_full_text)

    items = []
    for r in records:
        pk = _pair_key(r["source_person_id"], r["target_person_id"])
        fd = first_decisions.get(pk)
        item = IdentityReviewItem(
            source_person_id=r["source_person_id"],
            source_name=r.get("source_name"),
            target_person_id=r["target_person_id"],
            target_name=r.get("target_name"),
            confidence=r.get("confidence", 0),
            reason=r.get("reason", ""),
            evidence=r.get("evidence"),
            source_passages=r.get("source_passages", []),
            target_passages=r.get("target_passages", []),
            hops=fd[0] if fd else None,
            llm_decision=fd[1] if fd else None,
            llm_used_full_text=fd[2] if fd else False,
            annotated=pk in annotated_pairs,
        )
        items.append(item)

    return IdentityReviewListResponse(pending_count=len(items), items=items)


@router.get(
    "/identity/{source_id}/{target_id}/evidence",
    response_model=IdentityEvidenceResponse,
)
def get_review_evidence(
    source_id: int,
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取两个候选同人的证据包及图谱元素。"""

    repo = GraphRepository()
    source_evidence = repo.get_person_evidence_bundle(person_id=source_id, max_hops=3)
    target_evidence = repo.get_person_evidence_bundle(person_id=target_id, max_hops=3)

    graph_elements = repo.get_person_neighborhood_graph_elements(
        [source_id, target_id],
        max_hops=2,
    )

    review_rel = repo.run_read_query(
        cypher="""
        MATCH (s:Person_Nodes {person_id: $source})-[r:可能同人]->(t:Person_Nodes {person_id: $target})
        RETURN r.confidence AS confidence, r.reason AS reason, r.evidence AS evidence
        """,
        parameters={"source": source_id, "target": target_id},
    )
    review_records = review_rel.get("records", [])
    review_record = review_records[0] if review_records else None

    # LLM 逐轮同名判断留痕（与图关系无关，自动合并/判不同人的对也能看到判断理由）
    log_rows = (
        db.query(IdentityResolutionDecisionLog)
        .filter(
            or_(
                and_(
                    IdentityResolutionDecisionLog.new_person_id == source_id,
                    IdentityResolutionDecisionLog.candidate_person_id == target_id,
                ),
                and_(
                    IdentityResolutionDecisionLog.new_person_id == target_id,
                    IdentityResolutionDecisionLog.candidate_person_id == source_id,
                ),
            )
        )
        .order_by(
            IdentityResolutionDecisionLog.created_at,
            IdentityResolutionDecisionLog.hop,
        )
        .all()
    )

    def _parse_list(raw: str | None) -> list[str]:
        try:
            value = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    decision_logs = [
        IdentityDecisionLog(
            hop=row.hop,
            decision=row.decision,
            confidence=row.confidence,
            reason=row.reason or "",
            positive_evidence=_parse_list(row.positive_evidence_json),
            negative_evidence=_parse_list(row.negative_evidence_json),
            missing_evidence=_parse_list(row.missing_evidence_json),
            used_full_text=row.used_full_text,
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in log_rows
    ]

    source_doc_ids = repo.get_person_passage_doc_ids(source_id)
    target_doc_ids = repo.get_person_passage_doc_ids(target_id)
    all_doc_ids = list(set(source_doc_ids + target_doc_ids))

    passage_map: dict[int, Passage] = {}
    if all_doc_ids:
        passages = db.query(Passage).filter(Passage.doc_id.in_(all_doc_ids)).all()
        passage_map = {p.doc_id: p for p in passages}

    source_passage_texts = [
        PassageText(doc_id=did, title=passage_map[did].title, context=passage_map[did].context)
        for did in source_doc_ids
        if did in passage_map
    ]
    target_passage_texts = [
        PassageText(doc_id=did, title=passage_map[did].title, context=passage_map[did].context)
        for did in target_doc_ids
        if did in passage_map
    ]

    return IdentityEvidenceResponse(
        source_evidence=source_evidence,
        target_evidence=target_evidence,
        graph_elements=graph_elements,
        review_relation=review_record,
        decision_logs=decision_logs,
        source_passage_texts=source_passage_texts,
        target_passage_texts=target_passage_texts,
    )


@router.post(
    "/identity/{source_id}/{target_id}/annotate",
    response_model=AnnotateResponse,
)
def annotate_identity(
    source_id: int,
    target_id: int,
    payload: AnnotateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存人工标注（不改写图数据库）。

    置信度 1~10 即方向信号：1 表示非常确定不是同人，10 表示非常确定是同人。
    decision 未显式提供时按置信度推导（>=6 视为同人，<=5 视为不同人）。
    """

    if payload.decision is None:
        decision = "merge" if payload.human_confidence >= 6 else "keep_separate"
    elif payload.decision in ("merge", "keep_separate"):
        decision = payload.decision
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision 必须为 merge 或 keep_separate。",
        )

    repo = GraphRepository()
    names = repo.run_read_query(
        cypher="""
        MATCH (a:Person_Nodes {person_id: $s}), (b:Person_Nodes {person_id: $t})
        RETURN a.name AS source_name, b.name AS target_name
        """,
        parameters={"s": source_id, "t": target_id},
    )
    name_record = (names.get("records") or [{}])[0]

    key = _pair_key(source_id, target_id)
    existing = (
        db.query(IdentityAnnotation)
        .filter(
            IdentityAnnotation.source_person_id == key[0],
            IdentityAnnotation.target_person_id == key[1],
            IdentityAnnotation.annotator_id == current_user.id,
        )
        .first()
    )

    if existing:
        existing.decision = decision
        existing.human_confidence = payload.human_confidence
        existing.note = payload.note
        existing.source_name = name_record.get("source_name")
        existing.target_name = name_record.get("target_name")
        db.commit()
        return AnnotateResponse(status="updated", annotation_id=existing.id)

    annotation = IdentityAnnotation(
        source_person_id=key[0],
        target_person_id=key[1],
        source_name=name_record.get("source_name"),
        target_name=name_record.get("target_name"),
        decision=decision,
        human_confidence=payload.human_confidence,
        note=payload.note,
        annotator_id=current_user.id,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)

    return AnnotateResponse(status="success", annotation_id=annotation.id)


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
