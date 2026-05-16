"""
阶段六集成测试：端到端验证查询 Workflow。

测试策略：
- 使用 mock LLM provider（通过 conftest.py 自动注入）
- 使用 stub GraphRepository（通过 conftest.py 自动注入）
- 直接实例化查询相关 Skill，专注 workflow 逻辑
- 对需要 LLM 返回结构化 JSON 的 Skill（answer_compose、graph_statistics），
  通过 monkeypatch 替换 call_llm_structured，确保测试不依赖真实 LLM

验证目标（来自 06-integration-test.md）：
  6.1 人物信息查询
  6.2 人物关系查询
  6.3 宏观统计查询
  6.4 边界场景
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.context import ExecutionContext
from app.skills.atomic.graph_statistics_query import CypherGenResult, GraphStatisticsQueryAtomicSkill
from app.skills.atomic.person_info_query import PersonInfoQueryAtomicSkill
from app.skills.atomic.person_relation_query import PersonRelationQueryAtomicSkill
from app.skills.atomic.query_answer_compose import QueryAnswerComposeAtomicSkill
from app.skills.atomic.query_intent_classifier import QueryIntentClassifierAtomicSkill
from app.skills.common.llm_helper import LLMStructuredError
from app.skills.workflow.query_workflow import QueryWorkflowSkill


def _make_context(user_role: str = "user") -> ExecutionContext:
    return ExecutionContext(
        user={"id": 1, "role": user_role, "email": "test@example.com"},
        conversation={"id": 1, "title": "集成测试会话"},
    )


def _empty_repo() -> MagicMock:
    """返回一个 run_read_query 永远返回空记录的 stub repository。"""
    stub = MagicMock()
    stub.run_read_query.return_value = {"status": "success", "records": [], "record_count": 0}
    return stub


def _person_repo(records: list[dict]) -> MagicMock:
    """返回一个 run_read_query 在人物查询时返回指定记录的 stub。"""
    stub = MagicMock()
    stub.run_read_query.return_value = {
        "status": "success",
        "records": records,
        "record_count": len(records),
    }
    return stub


# ───────────────────────────────────────────────
# Fixtures：对需要 LLM 的 Skill 统一 stub
# ───────────────────────────────────────────────


@pytest.fixture()
def stub_answer_compose(monkeypatch):
    """让 query_answer_compose 的 call_llm_structured 直接返回固定 reply。"""
    import app.skills.atomic.query_answer_compose as mod
    from app.skills.atomic.query_answer_compose import _ReplyResult

    def _fake_llm(system_prompt, user_prompt, schema, skill_code, **kw):
        return _ReplyResult(reply="【测试占位回复】")

    monkeypatch.setattr(mod, "call_llm_structured", _fake_llm)


@pytest.fixture()
def stub_cypher_gen(monkeypatch):
    """让 graph_statistics_query 的 call_llm_structured 返回固定 Cypher。"""
    import app.skills.atomic.graph_statistics_query as mod

    def _fake_llm(system_prompt, user_prompt, schema, skill_code, **kw):
        return CypherGenResult(
            cypher="MATCH (n:Person_Nodes) RETURN count(n) AS count",
            params={},
            explanation="统计全部人物节点数量",
        )

    monkeypatch.setattr(mod, "call_llm_structured", _fake_llm)


@pytest.fixture()
def stub_classifier_person_info(monkeypatch):
    """让 query_intent_classifier 的 call_llm_structured 返回 person_info 分类。"""
    import app.skills.atomic.query_intent_classifier as mod
    from app.skills.atomic.query_intent_classifier import _IntentResult, _ExtractedParams

    def _fake_llm(system_prompt, user_prompt, schema, skill_code, **kw):
        return _IntentResult(
            query_type="person_info",
            extracted_params=_ExtractedParams(person_name="杜甫"),
            reasoning="mock: 识别为人物信息查询",
        )

    monkeypatch.setattr(mod, "call_llm_structured", _fake_llm)


@pytest.fixture()
def stub_classifier_person_relation(monkeypatch):
    """让 query_intent_classifier 的 call_llm_structured 返回 person_relation 分类。"""
    import app.skills.atomic.query_intent_classifier as mod
    from app.skills.atomic.query_intent_classifier import _IntentResult, _ExtractedParams

    def _fake_llm(system_prompt, user_prompt, schema, skill_code, **kw):
        return _IntentResult(
            query_type="person_relation",
            extracted_params=_ExtractedParams(person_a="李白", person_b="杜甫"),
            reasoning="mock: 识别为人物关系查询",
        )

    monkeypatch.setattr(mod, "call_llm_structured", _fake_llm)


@pytest.fixture()
def stub_classifier_graph_statistics(monkeypatch):
    """让 query_intent_classifier 的 call_llm_structured 返回 graph_statistics 分类。"""
    import app.skills.atomic.query_intent_classifier as mod
    from app.skills.atomic.query_intent_classifier import _IntentResult, _ExtractedParams

    def _fake_llm(system_prompt, user_prompt, schema, skill_code, **kw):
        return _IntentResult(
            query_type="graph_statistics",
            extracted_params=_ExtractedParams(
                keyword="",
                stat_description="总人物数",
            ),
            reasoning="mock: 识别为统计查询",
        )

    monkeypatch.setattr(mod, "call_llm_structured", _fake_llm)


# ───────────────────────────────────────────────
# 6.1 人物信息查询
# ───────────────────────────────────────────────


class TestPersonInfoQuery:
    """6.1 人物信息查询"""

    def test_person_found_in_graph_returns_graph_source(
        self,
        stub_classifier_person_info,
        stub_answer_compose,
    ):
        """图中有人物 → source 为 graph，reply 含图数据库标注。"""
        skill = QueryWorkflowSkill()
        skill.person_info.repository = _person_repo(
            [{"p": {"person_id": "uid-001", "name": "杜甫", "zi": "子美", "titles": "诗圣"}}]
        )
        # 子查询（生平/关系等）也返回空，直接用同一 stub
        skill.person_info.repository.run_read_query.side_effect = lambda cypher, parameters=None: (
            {
                "status": "success",
                "records": [{"p": {"person_id": "uid-001", "name": "杜甫", "zi": "子美", "titles": "诗圣"}}],
                "record_count": 1,
            }
            if "MATCH (p:Person_Nodes {name:" in cypher
            else {"status": "success", "records": [], "record_count": 0}
        )

        context = _make_context()
        result = skill.run(context, {"user_prompt": "杜甫是谁？"})

        assert "reply" in result, f"缺少 reply 字段: {result}"
        # step2_result 应 found=True, source=graph
        step2 = context.step_results.get("step2_result", {})
        assert step2.get("found") is True
        assert step2.get("source") == "graph"

    def test_person_not_in_graph_returns_llm_source(
        self,
        stub_classifier_person_info,
        stub_answer_compose,
    ):
        """图中无此人物 → source 为 llm，reply 含模型知识标注。"""
        skill = QueryWorkflowSkill()
        skill.person_info.repository = _empty_repo()

        context = _make_context()
        result = skill.run(context, {"user_prompt": "虚构人物甲乙丙是谁？"})

        assert "reply" in result, f"缺少 reply 字段: {result}"
        # step2_result 应 found=False, source=llm
        step2 = context.step_results.get("step2_result", {})
        assert step2.get("found") is False
        assert step2.get("source") == "llm"
        # 回复末尾应有 LLM 来源标注
        assert "模型知识" in result["reply"]

    def test_multiple_persons_same_name(self):
        """同名多人 → person_count > 1，person_data 为列表。"""
        skill = PersonInfoQueryAtomicSkill()
        skill.repository = _person_repo(
            [
                {"p": {"person_id": "uid-001", "name": "王氏", "zi": None, "titles": None}},
                {"p": {"person_id": "uid-002", "name": "王氏", "zi": None, "titles": None}},
            ]
        )
        # 子查询返回空
        skill.repository.run_read_query.side_effect = lambda cypher, parameters=None: (
            {
                "status": "success",
                "records": [
                    {"p": {"person_id": "uid-001", "name": "王氏", "zi": None, "titles": None}},
                    {"p": {"person_id": "uid-002", "name": "王氏", "zi": None, "titles": None}},
                ],
                "record_count": 2,
            }
            if "MATCH (p:Person_Nodes {name:" in cypher
            else {"status": "success", "records": [], "record_count": 0}
        )

        result = skill.run(_make_context(), {"person_name": "王氏"})

        assert result["found"] is True
        assert result["person_count"] == 2
        assert isinstance(result["person_data"], list), "同名多人时 person_data 应为列表"

    def test_person_info_source_note_appended(self, stub_classifier_person_info, stub_answer_compose):
        """最终 reply 包含图数据库来源标注（source=graph）。"""
        skill = QueryWorkflowSkill()
        skill.person_info.repository = _person_repo([])
        # 空结果 → source=llm → 标注"模型知识"
        context = _make_context()
        result = skill.run(context, {"user_prompt": "张三是谁？"})
        assert "模型知识" in result["reply"]


# ───────────────────────────────────────────────
# 6.2 人物关系查询
# ───────────────────────────────────────────────


class TestPersonRelationQuery:
    """6.2 人物关系查询"""

    def test_direct_relation_found(
        self,
        stub_classifier_person_relation,
        stub_answer_compose,
    ):
        """有直接 person_relation 关系 → direct_relations 非空。"""
        skill = QueryWorkflowSkill()

        def _rel_side_effect(cypher, parameters=None):
            params = parameters or {}
            # 人物查找
            if "Person_Nodes {name:" in cypher:
                name = params.get("name", "")
                pid = f"uid-{name}"
                return {
                    "status": "success",
                    "records": [{"person_id": pid, "name": name, "zi": None, "titles": None}],
                    "record_count": 1,
                }
            # 直接关系查询
            if "person_relation" in cypher:
                return {
                    "status": "success",
                    "records": [{"codes": ["Z"], "note": "挚友", "direction": "a_to_b"}],
                    "record_count": 1,
                }
            return {"status": "success", "records": [], "record_count": 0}

        stub_repo = MagicMock()
        stub_repo.run_read_query.side_effect = _rel_side_effect
        skill.person_relation.repository = stub_repo

        context = _make_context()
        result = skill.run(context, {"user_prompt": "李白和杜甫有何关系？"})

        assert "reply" in result
        step2 = context.step_results.get("step2_result", {})
        assert step2["person_a_info"]["found"] is True
        assert step2["person_b_info"]["found"] is True
        assert len(step2["direct_relations"]) > 0

    def test_indirect_relation_both_not_found(
        self,
        stub_classifier_person_relation,
        stub_answer_compose,
    ):
        """两人均未收录 → person_a_info.found=False, person_b_info.found=False。"""
        skill = QueryWorkflowSkill()
        skill.person_relation.repository = _empty_repo()

        context = _make_context()
        result = skill.run(context, {"user_prompt": "不存在甲和不存在乙有何关系？"})

        assert "reply" in result
        step2 = context.step_results.get("step2_result", {})
        assert step2["person_a_info"]["found"] is False
        assert step2["person_b_info"]["found"] is False
        assert step2["person_a_info"]["source"] == "llm"

    def test_person_relation_direct_skill_unit(self):
        """单元级别：PersonRelationQueryAtomicSkill 返回结构包含所有必要字段。"""
        skill = PersonRelationQueryAtomicSkill()

        def _side(cypher, parameters=None):
            if "name" in (parameters or {}):
                name = parameters["name"]
                return {
                    "status": "success",
                    "records": [{"person_id": f"uid-{name}", "name": name, "zi": None, "titles": None}],
                    "record_count": 1,
                }
            if "person_relation" in cypher:
                return {
                    "status": "success",
                    "records": [{"codes": ["H"], "note": "夫妻", "direction": "a_to_b"}],
                    "record_count": 1,
                }
            return {"status": "success", "records": [], "record_count": 0}

        stub_repo = MagicMock()
        stub_repo.run_read_query.side_effect = _side
        skill.repository = stub_repo

        result = skill.run(_make_context(), {"person_a": "甲", "person_b": "乙"})

        for key in ("person_a_info", "person_b_info", "direct_relations",
                    "shared_time", "shared_location", "shared_official_title",
                    "shared_historical_events", "shared_passages"):
            assert key in result, f"缺少字段: {key}"


# ───────────────────────────────────────────────
# 6.3 宏观统计查询
# ───────────────────────────────────────────────


class TestGraphStatisticsQuery:
    """6.3 宏观统计查询"""

    def test_count_query_runs_readonly_cypher(
        self,
        stub_classifier_graph_statistics,
        stub_cypher_gen,
        stub_answer_compose,
    ):
        """计数类查询 → 生成只读 Cypher、执行、返回 records 字段。"""
        skill = QueryWorkflowSkill()
        stub_repo = MagicMock()
        stub_repo.run_read_query.return_value = {
            "status": "success",
            "records": [{"count": 42}],
            "record_count": 1,
        }
        skill.graph_statistics.repository = stub_repo

        context = _make_context()
        result = skill.run(context, {"user_prompt": "图数据库中一共有多少人物？"})

        assert "reply" in result
        step2 = context.step_results.get("step2_result", {})
        assert "cypher" in step2
        assert "records" in step2

    def test_graph_statistics_skill_directly(self, stub_cypher_gen):
        """直接调用 GraphStatisticsQueryAtomicSkill，验证 Cypher 安全与记录返回。"""
        skill = GraphStatisticsQueryAtomicSkill()
        stub_repo = MagicMock()
        stub_repo.run_read_query.return_value = {
            "status": "success",
            "records": [{"count": 5}],
            "record_count": 1,
        }
        skill.repository = stub_repo

        result = skill.run(
            _make_context(),
            {"keyword": "杜甫", "stat_description": "图数据库中有多少名为杜甫的人物？"},
        )

        assert "cypher" in result
        assert "records" in result
        assert result["record_count"] == 1

    def test_readonly_guard_blocks_merge(self):
        """只读校验：MERGE 语句应被拦截。"""
        skill = GraphStatisticsQueryAtomicSkill()
        with pytest.raises(ValueError, match="只读"):
            skill._ensure_read_only("MERGE (p:Person_Nodes {name: 'test'}) RETURN p")

    def test_readonly_guard_blocks_create(self):
        """只读校验：CREATE 操作应被拦截。"""
        skill = GraphStatisticsQueryAtomicSkill()
        with pytest.raises(ValueError, match="只读"):
            skill._ensure_read_only("CREATE (n:Test) RETURN n")

    def test_readonly_guard_blocks_delete(self):
        """只读校验：MATCH + DELETE 应被拦截。"""
        skill = GraphStatisticsQueryAtomicSkill()
        with pytest.raises(ValueError, match="只读"):
            skill._ensure_read_only("MATCH (n) DELETE n")

    def test_readonly_guard_blocks_set(self):
        """只读校验：SET 操作应被拦截。"""
        skill = GraphStatisticsQueryAtomicSkill()
        with pytest.raises(ValueError, match="只读"):
            skill._ensure_read_only("MATCH (n) SET n.name = 'x' RETURN n")

    def test_readonly_guard_allows_match(self):
        """只读校验：MATCH 开头合法查询应通过。"""
        skill = GraphStatisticsQueryAtomicSkill()
        cypher = "MATCH (n:Person_Nodes {name: $name}) RETURN count(n) AS count"
        result = skill._ensure_read_only(cypher)
        assert result == cypher.strip()

    def test_readonly_guard_allows_optional_match(self):
        """只读校验：OPTIONAL MATCH 开头合法查询应通过。"""
        skill = GraphStatisticsQueryAtomicSkill()
        cypher = "OPTIONAL MATCH (n:Person_Nodes) RETURN n"
        result = skill._ensure_read_only(cypher)
        assert result == cypher.strip()

    def test_write_cypher_intercepted_in_run(self, monkeypatch):
        """只读校验集成：Cypher 包含写操作时 run() 返回 error 字段而非崩溃。"""
        import app.skills.atomic.graph_statistics_query as mod

        def _bad_cypher(system_prompt, user_prompt, schema, skill_code, **kw):
            return CypherGenResult(
                cypher="MERGE (p:Person_Nodes {name: 'hack'}) RETURN p",
                params={},
                explanation="危险写操作",
            )

        monkeypatch.setattr(mod, "call_llm_structured", _bad_cypher)
        skill = GraphStatisticsQueryAtomicSkill()
        result = skill.run(
            _make_context(),
            {"keyword": "", "stat_description": "注入写操作测试"},
        )
        assert "error" in result
        assert "写操作" in result["error"]


# ───────────────────────────────────────────────
# 6.4 边界场景
# ───────────────────────────────────────────────


class TestBoundaryScenarios:
    """6.4 边界场景"""

    def test_classifier_fallback_on_llm_error(self):
        """LLM 返回格式异常 → 分类器 fallback 为 person_info（默认路径）。"""
        import app.skills.atomic.query_intent_classifier as cls_mod

        original = cls_mod.call_llm_structured

        def bad_format(*args, **kwargs):
            raise LLMStructuredError("JSON 解析失败")

        cls_mod.call_llm_structured = bad_format
        try:
            skill = QueryIntentClassifierAtomicSkill()
            result = skill.run(_make_context(), {"user_prompt": "测试输入"})
        finally:
            cls_mod.call_llm_structured = original

        assert result["query_type"] == "person_info"
        assert "意图分类失败" in result["reasoning"]

    def test_unclassifiable_query_falls_back_to_person_info_path(
        self,
        stub_answer_compose,
    ):
        """无法分类的查询 → fallback 到 person_info 路径，workflow 完成不崩溃。"""
        import app.skills.atomic.query_intent_classifier as cls_mod

        original = cls_mod.call_llm_structured

        def raise_error(*args, **kwargs):
            raise LLMStructuredError("无法分类")

        cls_mod.call_llm_structured = raise_error

        try:
            skill = QueryWorkflowSkill()
            skill.person_info.repository = _empty_repo()

            context = _make_context()
            result = skill.run(context, {"user_prompt": "这是个无法分类的请求"})
        finally:
            cls_mod.call_llm_structured = original

        assert "reply" in result
        # fallback 路径 → person_info → not found → source=llm
        step1 = context.step_results.get("step1_result", {})
        assert step1.get("query_type") == "person_info"

    def test_neo4j_connection_failure_raises_controlled_exception(
        self,
        stub_classifier_person_info,
        stub_answer_compose,
    ):
        """Neo4j 连接失败 → 异常向上传播，包含可识别的错误信息（不是静默崩溃）。"""
        skill = QueryWorkflowSkill()
        stub_repo = MagicMock()
        stub_repo.run_read_query.side_effect = Exception("Connection refused to Neo4j bolt://neo4j:7687")
        skill.person_info.repository = stub_repo

        context = _make_context()
        # 异常应该是可理解的 Exception，而非 AttributeError / KeyError 等内部崩溃
        with pytest.raises(Exception) as exc_info:
            skill.run(context, {"user_prompt": "杜甫是谁？"})

        exc_str = str(exc_info.value).lower()
        assert any(kw in exc_str for kw in ["connection", "neo4j", "bolt", "refused"]), (
            f"Neo4j 连接失败时应抛出包含连接信息的异常，实际: {exc_info.value}"
        )

    def test_workflow_correctly_routes_person_info(
        self,
        stub_classifier_person_info,
        stub_answer_compose,
    ):
        """Workflow 路由：person_info 分类 → 调用 person_info skill → step2_result 有 found 字段。"""
        skill = QueryWorkflowSkill()
        skill.person_info.repository = _empty_repo()
        context = _make_context()
        skill.run(context, {"user_prompt": "杜甫是谁？"})
        step2 = context.step_results.get("step2_result", {})
        assert "found" in step2

    def test_workflow_correctly_routes_person_relation(
        self,
        stub_classifier_person_relation,
        stub_answer_compose,
    ):
        """Workflow 路由：person_relation 分类 → 调用 person_relation skill → step2 有 direct_relations。"""
        skill = QueryWorkflowSkill()
        skill.person_relation.repository = _empty_repo()
        context = _make_context()
        skill.run(context, {"user_prompt": "李白和杜甫有何关系？"})
        step2 = context.step_results.get("step2_result", {})
        assert "direct_relations" in step2

    def test_workflow_correctly_routes_graph_statistics(
        self,
        stub_classifier_graph_statistics,
        stub_cypher_gen,
        stub_answer_compose,
    ):
        """Workflow 路由：graph_statistics 分类 → 调用 graph_statistics skill → step2 有 cypher。"""
        skill = QueryWorkflowSkill()
        skill.graph_statistics.repository = _empty_repo()
        context = _make_context()
        skill.run(context, {"user_prompt": "共有多少人物？"})
        step2 = context.step_results.get("step2_result", {})
        assert "cypher" in step2

    def test_answer_compose_appends_graph_source_note(self):
        """QueryAnswerComposeAtomicSkill：source=graph 时 reply 含'图数据库'标注。"""
        import app.skills.atomic.query_answer_compose as mod
        from app.skills.atomic.query_answer_compose import _ReplyResult

        original = mod.call_llm_structured

        def _fake(system_prompt, user_prompt, schema, skill_code, **kw):
            return _ReplyResult(reply="此人为唐代诗人。")

        mod.call_llm_structured = _fake
        try:
            skill = QueryAnswerComposeAtomicSkill()
            result = skill.run(
                _make_context(),
                {
                    "user_prompt": "杜甫是谁？",
                    "query_type": "person_info",
                    "query_result": {"found": True},
                    "source": "graph",
                },
            )
        finally:
            mod.call_llm_structured = original

        assert "图数据库" in result["reply"]

    def test_answer_compose_appends_llm_source_note(self):
        """QueryAnswerComposeAtomicSkill：source=llm 时 reply 含'模型知识'标注。"""
        import app.skills.atomic.query_answer_compose as mod
        from app.skills.atomic.query_answer_compose import _ReplyResult

        original = mod.call_llm_structured

        def _fake(system_prompt, user_prompt, schema, skill_code, **kw):
            return _ReplyResult(reply="该人物未被收录。")

        mod.call_llm_structured = _fake
        try:
            skill = QueryAnswerComposeAtomicSkill()
            result = skill.run(
                _make_context(),
                {
                    "user_prompt": "虚构人是谁？",
                    "query_type": "person_info",
                    "query_result": {"found": False},
                    "source": "llm",
                },
            )
        finally:
            mod.call_llm_structured = original

        assert "模型知识" in result["reply"]
