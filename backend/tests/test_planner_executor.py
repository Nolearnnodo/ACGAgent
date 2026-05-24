"""Agent 核心 + 功能 A workflow 基础测试。

这些测试聚焦于流水线最小闭环是否成立，不强求 LLM 真实可用：
- mock LLM provider 不返回 JSON 时各 Skill 应走 fallback；
- GraphRepository 在 conftest 中已被 stub 掉，不连真 Neo4j。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision, PlannerInput
from app.agents.planner import Planner
from app.graph.repository import GraphRepository
from app.models.passage import Passage
from app.services.passage_service import PassageService
from app.skills.atomic.person_identity_resolution import (
    IdentityResolutionDecision,
    PersonIdentityResolutionAtomicSkill,
)
from app.skills.workflow.passage_ingestion_workflow import (
    PassageIngestionWorkflowSkill,
    _has_graph_write_warning,
)


class _DummyQuery:
    def __init__(self, result, all_result=None):
        self.result = result
        self.all_result = all_result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.all_result or []


class _DummySession:
    """极简 Session 模拟器，避免测试依赖真实数据库。"""

    def __init__(self, query_result=None, query_all_result=None):
        self.records = []
        self.query_result = query_result
        self.query_all_result = query_all_result

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = len(self.records) + 1
        self.records.append(item)

    def commit(self):
        return None

    def refresh(self, item):
        return None

    def query(self, model):
        return _DummyQuery(self.query_result, self.query_all_result)

    def get(self, model, key):
        return None


# ---------------- Planner / Executor 基线 ----------------


def test_planner_rejects_non_admin_write_request():
    planner = Planner()
    decision = planner.plan(
        planner_input=PlannerInput(
            user_input="请帮我写入图数据库",
            user_id=1,
            user_role="user",
            conversation_id=1,
            recent_messages=[],
        )
    )

    assert decision.decision_type == "reject"
    assert decision.target_skill_code == "permission_denied"


def test_executor_runs_conversation_reply_workflow():
    executor = Executor()
    db = _DummySession()
    context = ExecutionContext(
        user={"id": 1, "role": "user", "email": "demo@example.com"},
        conversation={"id": 1, "title": "测试会话"},
    )
    decision = PlannerDecision(
        intent="general_request",
        decision_type="workflow",
        target_skill_code="conversation_reply_workflow",
        reason="测试执行",
        arguments={"user_prompt": "你好"},
    )

    result = executor.execute(
        db=db,
        context=context,
        planner_decision=decision,
        planner_record_id=1,
        trigger_message_id=1,
    )

    assert result.success is True
    assert "reply" in result.output
    assert context.step_results["step1_result"] == result.output


# ---------------- 功能 A workflow ----------------


def _build_passage_context(title: str, content: str, doc_id: int = 9999) -> ExecutionContext:
    return ExecutionContext(
        user={"id": 1, "role": "admin", "email": "admin@example.com"},
        conversation={},
        metadata={
            "trigger_type": "passage_upload",
            "passage_id": doc_id,
            "passage": {
                "doc_id": doc_id,
                "title": title,
                "context": content,
                "source_type": "upload",
                "source_file": f"test_data/{title}.txt",
            },
        },
    )


def test_passage_ingestion_workflow_skeleton(tmp_path):
    """LLM 不可用时，整个流水线应走 fallback 跑通并落 YAML。"""

    skill = PassageIngestionWorkflowSkill()
    context = _build_passage_context(
        title="安定胡永府君墓誌",
        content="君諱永，字敬延，安定臨涇人也。" * 5,
        doc_id=8001,
    )
    context.metadata["output_dir"] = str(tmp_path)

    result = skill.run(context, arguments={})

    assert result["status"] in {"success", "partial"}
    assert "output_path" in result
    assert Path(result["output_path"]).exists()
    # 必须出现 5 段 step 产物
    for stage in (
        "probe_atomic",
        "passage_meta_atomic",
        "person_layer_atomic",
        "event_relation_atomic",
        "passage_format_output_atomic",
        "person_identity_resolution_atomic",
    ):
        assert stage in result["steps"]
    # context.metadata 里 probe / passage_meta / person_layer / event_relation 都要有
    for key in (
        "probe",
        "passage_meta",
        "person_layer",
        "event_relation",
        "person_identity_resolution",
    ):
        assert key in context.metadata


def test_person_identity_resolution_merges_only_after_llm_same(monkeypatch):
    result, repo, context = _run_identity_resolution(
        monkeypatch,
        [
            IdentityResolutionDecision(
                decision="same",
                confidence=0.95,
                positive_evidence=["同名且旁证一致"],
                negative_evidence=[],
                missing_evidence=[],
                next_hop_focus=[],
                reason="证据充分",
            )
        ],
    )

    assert result["merged_person_count"] == 1
    assert repo.merges == [(8001001, 9001001)]
    assert context.metadata["person_identity_resolution"]["status"] == "success"


class _IdentityResolutionRepo:
    def __init__(self, review_raises: bool = False):
        self.merges: list[tuple[int, int]] = []
        self.reviews: list[dict] = []
        self.evidence_calls: list[tuple[int, int, tuple[str, ...]]] = []
        self.review_raises = review_raises

    def find_same_name_person_candidates_for_passage(self, doc_id):
        return {
            "records": [
                {
                    "name": "李某",
                    "new_person_id": 9001001,
                    "candidate_person_id": 8001001,
                    "new_passage_doc_id": doc_id,
                    "candidate_passage_doc_id": 8001,
                    "candidate_count_for_new_person": 1,
                }
            ]
        }

    def get_person_evidence_bundle(self, person_id, max_hops=2, focus=None):
        self.evidence_calls.append((person_id, max_hops, tuple(focus or [])))
        return {
            "person": {"person_id": person_id, "name": "李某"},
            "passages": [{"doc_id": 9001 if person_id == 9001001 else 8001}],
        }

    def merge_person_nodes(self, canonical_person_id, duplicate_person_id):
        self.merges.append((canonical_person_id, duplicate_person_id))
        return {"status": "success"}

    def mark_possible_same_person(self, **kwargs):
        if self.review_raises:
            raise RuntimeError("review edge write failed")
        self.reviews.append(kwargs)
        return {"status": "success"}


def _run_identity_resolution(monkeypatch, decisions, repo=None):
    repo = repo or _IdentityResolutionRepo()
    decision_iter = iter(decisions)
    monkeypatch.setattr("app.skills.atomic.person_identity_resolution.GraphRepository", lambda: repo)
    monkeypatch.setattr(
        "app.skills.atomic.person_identity_resolution._call_identity_llm",
        lambda **kwargs: next(decision_iter),
    )
    context = _build_passage_context("测试墓志", "测试正文", doc_id=9001)
    result = PersonIdentityResolutionAtomicSkill().run(context, {})
    return result, repo, context


def test_person_identity_resolution_keeps_different_people_separate(monkeypatch):
    result, repo, _context = _run_identity_resolution(
        monkeypatch,
        [
            IdentityResolutionDecision(
                decision="different",
                confidence=0.91,
                negative_evidence=["任官时间冲突"],
                reason="证据冲突",
            )
        ],
    )

    assert result["merged_person_count"] == 0
    assert result["review_link_count"] == 0
    assert result["cases"][0]["action"] == "kept_separate"
    assert repo.merges == []
    assert repo.reviews == []


def test_person_identity_resolution_enters_manual_review_after_five_hops(monkeypatch):
    result, repo, _context = _run_identity_resolution(
        monkeypatch,
        [
            IdentityResolutionDecision(
                decision="insufficient",
                confidence=0.2,
                missing_evidence=["亲属链不足"],
                next_hop_focus=["relations"],
                reason="继续查关系",
            )
            for _ in range(4)
        ],
    )

    assert result["merged_person_count"] == 0
    assert result["review_link_count"] == 1
    assert result["cases"][0]["action"] == "manual_review"
    assert result["cases"][0]["hops_used"] == 5
    assert repo.merges == []
    assert len(repo.reviews) == 1
    assert [call[1] for call in repo.evidence_calls[::2]] == [2, 3, 4, 5]


def test_person_identity_resolution_low_confidence_same_goes_to_manual_review(monkeypatch):
    result, repo, _context = _run_identity_resolution(
        monkeypatch,
        [
            IdentityResolutionDecision(
                decision="same",
                confidence=0.5,
                positive_evidence=["同名"],
                next_hop_focus=["events"],
                reason="置信度不足",
            )
            for _ in range(4)
        ],
    )

    assert result["merged_person_count"] == 0
    assert result["review_link_count"] == 1
    assert result["cases"][0]["action"] == "manual_review"
    assert repo.merges == []
    assert len(repo.reviews) == 1


def test_person_identity_resolution_marks_failure_when_review_link_fails(monkeypatch):
    result, repo, context = _run_identity_resolution(
        monkeypatch,
        [
            IdentityResolutionDecision(
                decision="insufficient",
                confidence=0.2,
                missing_evidence=["旁证不足"],
                next_hop_focus=["relations"],
                reason="继续查关系",
            )
            for _ in range(4)
        ],
        repo=_IdentityResolutionRepo(review_raises=True),
    )

    assert result["status"] == "partial"
    assert result["failed_resolution_count"] == 1
    assert result["review_link_count"] == 0
    assert result["cases"][0]["action"] == "failed"
    assert "review_link_failed" in result["cases"][0]["error"]
    assert repo.merges == []
    assert repo.reviews == []
    assert any(
        warning.get("type") == "function_b_review_link_failed"
        for warning in context.metadata["warnings"]
    )


def test_merge_person_nodes_runs_inside_single_write_transaction():
    class _Summary:
        query_type = "w"
        database = "neo4j"

        class counters:
            contains_updates = True
            nodes_created = 0
            nodes_deleted = 0
            relationships_created = 0
            relationships_deleted = 0
            properties_set = 0
            labels_added = 0
            labels_removed = 0

    class _Result:
        def __iter__(self):
            return iter([])

        def consume(self):
            return _Summary()

    class _Tx:
        def __init__(self):
            self.calls = []

        def run(self, cypher, parameters):
            self.calls.append((cypher, parameters))
            return _Result()

    class _Session:
        def __init__(self):
            self.tx = _Tx()
            self.execute_write_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute_write(self, fn):
            self.execute_write_calls += 1
            return fn(self.tx)

    class _Driver:
        def __init__(self):
            self.session_obj = _Session()

        def session(self, database):
            assert database == "neo4j"
            return self.session_obj

    class _Client:
        def __init__(self):
            self.settings = SimpleNamespace(neo4j_database="neo4j")
            self.driver = _Driver()

        def get_driver(self):
            return self.driver

    client = _Client()
    result = GraphRepository(client=client).merge_person_nodes(
        canonical_person_id=8001001,
        duplicate_person_id=9001001,
    )

    assert result["status"] == "success"
    assert result["step_count"] == 7
    assert client.driver.session_obj.execute_write_calls == 1
    assert len(client.driver.session_obj.tx.calls) == 7
    assert all(
        params == {"canonical_person_id": 8001001, "duplicate_person_id": 9001001}
        for _cypher, params in client.driver.session_obj.tx.calls
    )


def test_passage_ingestion_workflow_on_real_test_data(tmp_path):
    """直接读 test_data/ 里的真实墓志铭样本端到端跑。"""

    sample = Path(__file__).resolve().parents[2] / "test_data" / "墓志铭" / "岸頭府校尉劉住隆妻王氏墓誌銘.txt"
    if not sample.exists():
        # CI 环境可能未带 test_data；标记跳过而非失败。
        import pytest

        pytest.skip("test_data 未挂载")

    text = sample.read_text(encoding="utf-8")
    context = _build_passage_context(
        title=sample.stem,
        content=text,
        doc_id=8002,
    )
    context.metadata["output_dir"] = str(tmp_path)

    skill = PassageIngestionWorkflowSkill()
    result = skill.run(context, arguments={})

    output_path = Path(result["output_path"])
    assert output_path.exists()
    rendered = output_path.read_text(encoding="utf-8")
    # 关键字段存在
    assert "passage_info" in rendered
    assert "persons" in rendered
    assert "stats" in rendered


# ---------------- PassageService ----------------


def test_passage_service_extract_text_via_markitdown_plain_text():
    """fast path：纯文本后缀直接 UTF-8 decode。"""

    service = PassageService(db=None)  # type: ignore[arg-type]
    title, ctx = service.extract_text_via_markitdown(
        "古籍一.txt", "正文内容\n第二行".encode("utf-8")
    )
    assert title == "古籍一"
    assert "正文内容" in ctx
    assert "第二行" in ctx


def test_passage_service_rejects_unknown_suffix():
    """非白名单后缀应直接报错。"""

    service = PassageService(db=None)  # type: ignore[arg-type]
    try:
        service.extract_text_via_markitdown("malware.exe", b"\x00\x01")
    except ValueError as exc:
        assert "不支持" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝 .exe")


def test_passage_service_content_hash_normalizes_minor_whitespace():
    service = PassageService(db=None)  # type: ignore[arg-type]

    first = service.compute_content_hash("正文内容\r\n第二行   \n")
    second = service.compute_content_hash("正文内容\n第二行")
    changed = service.compute_content_hash("正文内容\n第三行")

    assert first == second
    assert first != changed


def test_passage_service_create_passage_sets_content_hash():
    db = _DummySession()
    service = PassageService(db=db)  # type: ignore[arg-type]
    user = SimpleNamespace(id=42)

    passage = service.create_passage(
        user=user,  # type: ignore[arg-type]
        title="古籍一",
        context="正文内容",
        source_type="upload",
        file_name="古籍一.txt",
    )

    assert passage.content_hash == service.compute_content_hash("正文内容")
    assert db.records[-1] is passage


def test_passage_service_finds_existing_by_content_hash():
    existing = Passage(
        title="古籍一",
        context="正文内容",
        source_type="upload",
        file_name="古籍一.txt",
        content_hash="abc123",
        created_by=1,
        workflow_status="success",
    )
    service = PassageService(db=_DummySession(query_result=existing))  # type: ignore[arg-type]

    assert service.find_by_content_hash("abc123") is existing


def test_passage_service_duplicate_lookup_backfills_legacy_hash():
    legacy = Passage(
        title="古籍一",
        context="正文内容\n第二行",
        source_type="upload",
        file_name="古籍一.txt",
        content_hash=None,
        created_by=1,
        workflow_status="success",
    )
    service = PassageService(
        db=_DummySession(query_result=None, query_all_result=[legacy])  # type: ignore[arg-type]
    )
    content_hash = service.compute_content_hash("正文内容\n第二行")

    result = service.find_duplicate_passage("正文内容\r\n第二行   ", content_hash)

    assert result is legacy
    assert legacy.content_hash == content_hash


def test_graph_write_warning_detection_ignores_dictionary_misses():
    warnings = [
        {"type": "unmatched_era", "detail": "era missing"},
        {"type": "unmatched_historic_event", "detail": "event missing"},
    ]

    assert _has_graph_write_warning(warnings) is False


def test_graph_write_warning_detection_flags_neo4j_write_failures():
    warnings = [
        {"type": "unmatched_historic_event", "detail": "event missing"},
        {"type": "neo4j_write_failed", "detail": "upsert_person failed"},
    ]

    assert _has_graph_write_warning(warnings) is True
