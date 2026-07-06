from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base_class import Base
from app.models.annotation import IdentityAIReport
from app.models.passage import Passage
from app.services.identity_report_service import (
    generate_all_same_name_identity_reports,
    get_identity_report_for_pair,
)


class _ReportRepo:
    def list_all_same_name_pairs(self):
        return {
            "records": [
                {
                    "source_person_id": 2002,
                    "source_name": "李某",
                    "target_person_id": 1001,
                    "target_name": "李某",
                },
                {
                    "source_person_id": 3003,
                    "source_name": "王某",
                    "target_person_id": 1001,
                    "target_name": "李某",
                },
            ]
        }

    def get_person_evidence_bundle(self, person_id, max_hops=3):
        return {
            "person": {"person_id": person_id, "name": "李某" if person_id != 3003 else "王某"},
            "passages": [{"doc_id": 1 if person_id == 1001 else 2, "title": "墓志"}],
            "life_events": [],
            "relations": [],
            "historical_events": [],
            "relation_paths": [],
            "related_person_evidence": [],
        }

    def get_person_passage_doc_ids(self, person_id):
        return [1 if person_id == 1001 else 2]


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all(
        [
            Passage(
                doc_id=1,
                title="旧墓志",
                context="旧文献原文",
                source_type="upload",
                created_by=1,
            ),
            Passage(
                doc_id=2,
                title="新墓志",
                context="新文献原文",
                source_type="upload",
                created_by=1,
            ),
        ]
    )
    db.commit()
    return db


def test_generate_all_same_name_identity_reports_creates_missing_reports_only():
    db = _session()
    db.add(
        IdentityAIReport(
            source_person_id=1001,
            target_person_id=2002,
            source_name="李某",
            target_name="李某",
            report_markdown="已有报告",
        )
    )
    db.commit()
    calls = []

    def fake_llm(payload):
        calls.append(payload)
        return "### 结论与置信度\n- **研判结论**：【证据不足，存疑】"

    result = generate_all_same_name_identity_reports(
        db=db,
        repo=_ReportRepo(),
        generate_report=fake_llm,
    )

    assert result["pair_count"] == 2
    assert result["created_count"] == 1
    assert result["skipped_count"] == 1
    assert len(calls) == 1
    assert calls[0]["target_person_name"] == "王某"
    assert get_identity_report_for_pair(db, 3003, 1001).report_markdown.startswith("### 结论")


def test_generate_all_same_name_identity_reports_force_regenerates_existing_reports():
    db = _session()
    db.add(
        IdentityAIReport(
            source_person_id=1001,
            target_person_id=2002,
            source_name="李某",
            target_name="李某",
            report_markdown="旧报告",
        )
    )
    db.commit()

    result = generate_all_same_name_identity_reports(
        db=db,
        repo=_ReportRepo(),
        generate_report=lambda payload: "新报告",
        force=True,
    )

    assert result["created_count"] == 1
    assert result["updated_count"] == 1
    assert get_identity_report_for_pair(db, 2002, 1001).report_markdown == "新报告"
