from types import SimpleNamespace

from app.api.v1.routers import review


class _Query:
    def __init__(self, rows=None):
        self.rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _DB:
    def query(self, model):
        return _Query([])


class _Repo:
    def get_person_evidence_bundle(self, person_id, max_hops=3):
        return {"person": {"person_id": person_id}, "passages": []}

    def get_person_neighborhood_graph_elements(self, person_ids, max_hops=2):
        return {"nodes": [], "edges": []}

    def run_read_query(self, cypher, parameters=None):
        return {"records": []}

    def get_person_passage_doc_ids(self, person_id):
        return []


def test_get_review_evidence_includes_ai_report(monkeypatch):
    report = SimpleNamespace(
        id=7,
        source_person_id=1001,
        target_person_id=2002,
        source_name="李某",
        target_name="李某",
        report_markdown="### 结论与置信度\n- **同人置信度**：5 / 10",
        updated_at=None,
    )
    monkeypatch.setattr(review, "GraphRepository", lambda: _Repo())
    monkeypatch.setattr(review, "get_identity_report_for_pair", lambda db, s, t: report)

    response = review.get_review_evidence(
        source_id=2002,
        target_id=1001,
        current_user=SimpleNamespace(id=1, role="user"),
        db=_DB(),
    )

    assert response.ai_report is not None
    assert response.ai_report.id == 7
    assert response.ai_report.report_markdown.startswith("### 结论")


def test_generate_identity_ai_reports_endpoint_returns_counts(monkeypatch):
    monkeypatch.setattr(
        review,
        "generate_all_same_name_identity_reports",
        lambda db, generated_by_id=None, force=False: {
            "pair_count": 3,
            "created_count": 2,
            "updated_count": 0,
            "skipped_count": 1,
            "failed_count": 0,
            "failures": [],
        },
    )

    response = review.generate_identity_ai_reports(
        payload=review.GenerateIdentityReportsRequest(force=False),
        current_user=SimpleNamespace(id=9, role="admin"),
        db=_DB(),
    )

    assert response.pair_count == 3
    assert response.created_count == 2
    assert response.skipped_count == 1
