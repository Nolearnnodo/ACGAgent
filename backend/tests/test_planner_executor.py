"""Agent 核心 + 功能 A workflow 基础测试。

这些测试聚焦于流水线最小闭环是否成立，不强求 LLM 真实可用：
- mock LLM provider 不返回 JSON 时各 Skill 应走 fallback；
- GraphRepository 在 conftest 中已被 stub 掉，不连真 Neo4j。
"""

from __future__ import annotations

from pathlib import Path

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision, PlannerInput
from app.agents.planner import Planner
from app.services.passage_service import PassageService
from app.skills.workflow.passage_ingestion_workflow import PassageIngestionWorkflowSkill


class _DummyQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class _DummySession:
    """极简 Session 模拟器，避免测试依赖真实数据库。"""

    def __init__(self):
        self.records = []

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = len(self.records) + 1
        self.records.append(item)

    def commit(self):
        return None

    def refresh(self, item):
        return None

    def query(self, model):
        return _DummyQuery(None)

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
    for stage in ("probe", "passage_meta", "person_layer", "event_relation", "format_output"):
        assert stage in result["steps"]
    # context.metadata 里 probe / passage_meta / person_layer / event_relation 都要有
    for key in ("probe", "passage_meta", "person_layer", "event_relation"):
        assert key in context.metadata


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
