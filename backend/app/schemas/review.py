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
    hops: int | None = None
    llm_decision: str | None = None
    llm_used_full_text: bool = False
    annotated: bool = False


class IdentityReviewListResponse(BaseModel):
    pending_count: int
    items: list[IdentityReviewItem]


class PassageText(BaseModel):
    doc_id: int
    title: str
    context: str


class IdentityDecisionLog(BaseModel):
    """LLM 逐轮同名判断留痕（来自 identity_resolution_decision_logs）。"""

    hop: int
    decision: str
    confidence: float = 0
    reason: str = ""
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    used_full_text: bool = False
    created_at: str | None = None


class IdentityAIReportRead(BaseModel):
    id: int
    source_person_id: int
    target_person_id: int
    source_name: str | None = None
    target_name: str | None = None
    report_markdown: str
    updated_at: str | None = None


class IdentityEvidenceResponse(BaseModel):
    source_evidence: dict[str, Any]
    target_evidence: dict[str, Any]
    graph_elements: dict[str, Any]
    review_relation: dict[str, Any] | None = None
    decision_logs: list[IdentityDecisionLog] = Field(default_factory=list)
    source_passage_texts: list[PassageText] = Field(default_factory=list)
    target_passage_texts: list[PassageText] = Field(default_factory=list)
    ai_report: IdentityAIReportRead | None = None


class GenerateIdentityReportsRequest(BaseModel):
    force: bool = False


class GenerateIdentityReportsResponse(BaseModel):
    pair_count: int
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
    failures: list[dict[str, Any]] = Field(default_factory=list)


class AdjudicateRequest(BaseModel):
    decision: str  # "merge" or "keep_separate"


class AdjudicateResponse(BaseModel):
    status: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AnnotateRequest(BaseModel):
    # 置信度本身即方向信号（0=非常确定不是同人，10=非常确定是同人），
    # decision 可省略，由后端按置信度推导，仅为兼容旧数据保留。
    human_confidence: int = Field(ge=0, le=10)
    decision: str | None = None
    note: str = ""


class AnnotateResponse(BaseModel):
    status: str
    annotation_id: int
