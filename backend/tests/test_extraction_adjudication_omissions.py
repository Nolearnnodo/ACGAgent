"""双人标注的缺漏清单应进入裁定差异。"""

from app.schemas.extraction_annotation import AnnotationOmission, ExtractionAnnotationLabel
from app.services.extraction_adjudication_service import build_adjudication_differences


def test_omission_records_are_compared_in_adjudication():
    first = ExtractionAnnotationLabel(
        omissions=[
            AnnotationOmission(
                key="missing-person-1",
                category="人物缺漏",
                description="漏建李彦",
                target_path="人物基本信息 / 李彦",
                evidence_note="标题中有该人名。",
                resolution="resolved",
            )
        ]
    )
    second = ExtractionAnnotationLabel()

    differences = build_adjudication_differences(first, second)

    assert any(item.path == "omissions" and item.label == "差缺补漏" for item in differences)
