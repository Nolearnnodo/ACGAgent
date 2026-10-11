"""覆盖检查与缺漏不参与已标注项目的正确率。"""

import pytest

from app.schemas.extraction_annotation import AIAnnotationReviewDecision, AIAnnotationReviewOmission
from app.services.extraction_ai_annotation_review_service import (
    ExtractionAIAnnotationReviewError,
    ExtractionAIAnnotationReviewService,
)


def test_accuracy_only_uses_existing_annotation_items():
    decisions = [
        AIAnnotationReviewDecision(item_id="passage:source_type", verdict="correct"),
        AIAnnotationReviewDecision(item_id="person:p1:level", verdict="incorrect", correction="2 级"),
        AIAnnotationReviewDecision(item_id="coverage:persons", verdict="incorrect"),
        AIAnnotationReviewDecision(item_id="coverage:events", verdict="correct"),
    ]
    omissions = [AIAnnotationReviewOmission(id="missing-1", category="人物缺漏")]

    summary = ExtractionAIAnnotationReviewService._summary(decisions, omissions)

    assert summary.total_item_count == 2
    assert summary.correct_count == 1
    assert summary.incorrect_count == 1
    assert summary.omission_count == 1
    assert summary.accuracy == 0.5


def test_coverage_gap_requires_a_specific_record_in_the_right_category():
    decisions = [AIAnnotationReviewDecision(item_id="coverage:relations", verdict="incorrect")]
    with pytest.raises(ExtractionAIAnnotationReviewError, match="逐条登记"):
        ExtractionAIAnnotationReviewService._validate_submission(decisions, [], [])

    omissions = [
        AIAnnotationReviewOmission(
            id="missing-relation",
            category="关系缺漏",
            target_path="人物关系 / 李承与李允",
            description="漏建李承与李允的父子关系",
            correction="补建亲属关系，具体表现为父子",
            evidence_note="原文作“子李允”。",
        )
    ]
    ExtractionAIAnnotationReviewService._validate_submission(decisions, omissions, [])

    with pytest.raises(ExtractionAIAnnotationReviewError, match="不一致|统一检查结果"):
        ExtractionAIAnnotationReviewService._validate_submission(
            [AIAnnotationReviewDecision(item_id="coverage:relations", verdict="correct")],
            omissions,
            [],
        )
