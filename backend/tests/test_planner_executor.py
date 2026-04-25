"""Agent 核心基础测试。

这些测试聚焦在最小闭环是否成立，而不是覆盖所有实现细节。
"""

from app.agents.context import ExecutionContext
from app.agents.executor import Executor
from app.agents.models import PlannerDecision, PlannerInput
from app.agents.planner import Planner
from app.services.passage_service import PassageService


class DummyQuery:
    """极简 Query 模拟器。"""

    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class DummySession:
    """极简 Session 模拟器，用于避免测试依赖真实数据库。"""

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
        return DummyQuery(None)


def test_planner_rejects_non_admin_write_request():
    """普通用户请求图写入时，应被 Planner 直接拒绝。"""

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
    """Executor 应能执行最小 Workflow 并写入 step 结果。"""

    executor = Executor()
    db = DummySession()
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


def test_executor_runs_passage_ingestion_workflow():
    """管理员触发古籍 workflow 时，应能写入 passage 相关执行结果。"""

    executor = Executor()
    db = DummySession()
    context = ExecutionContext(
        user={"id": 1, "role": "admin", "email": "admin@example.com"},
        conversation={},
        metadata={
            "trigger_type": "passage_upload",
            "passage_id": 10,
            "passage": {
                "doc_id": 10,
                "title": "山海经",
                "context": "  古籍正文  ",
                "source_type": "upload",
            },
        },
    )
    decision = PlannerDecision(
        intent="passage_ingestion",
        decision_type="workflow",
        target_skill_code="passage_ingestion_workflow",
        reason="测试古籍处理流程",
        arguments={"doc_id": 10},
    )

    result = executor.execute(
        db=db,
        context=context,
        planner_decision=decision,
        planner_record_id=None,
        trigger_message_id=None,
    )

    assert result.success is True
    assert result.output["steps"]["preprocess"]["title"] == "山海经"
    assert context.step_results["step3_result"]["operation"] == "write"


def test_passage_service_extracts_markdown_title_and_content():
    """Markdown 上传时应能用文件名生成标题，并解析正文。"""

    service = PassageService(db=None)  # type: ignore[arg-type]
    title, context = service.extract_text_from_md("古籍一.md", "正文内容".encode("utf-8"))

    assert title == "古籍一"
    assert context == "正文内容"
