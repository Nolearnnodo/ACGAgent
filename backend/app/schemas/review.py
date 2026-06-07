"""人工审核相关数据模型。"""

from typing import Any

from pydantic import BaseModel, Field


class IdentityReviewItem(BaseModel):
    source_person_id: int
    source_name: str | None = None
    target_person_id: int
    target_name: str | None = None
    confidence: float = 0
    reason: str = ""
    evidence: dict[str, Any] | None = None
    source_passages: list[str] = Field(default_factory=list)
    target_passages: list[str] = Field(default_factory=list)


class IdentityReviewListResponse(BaseModel):
    pending_count: int
    items: list[IdentityReviewItem]


class IdentityEvidenceResponse(BaseModel):
    source_evidence: dict[str, Any]
    target_evidence: dict[str, Any]
    graph_elements: dict[str, Any]
    review_relation: dict[str, Any] | None = None


class AdjudicateRequest(BaseModel):
    decision: str  # "merge" or "keep_separate"


class AdjudicateResponse(BaseModel):
    status: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
