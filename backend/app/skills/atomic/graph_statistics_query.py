from __future__ import annotations

from pydantic import BaseModel

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import call_llm_structured

SYSTEM_PROMPT = """你是 Neo4j Cypher 只读统计查询生成器，专门用于古籍人物关系图谱。

图谱 Schema：
节点类型：
- Person_Nodes（person_id, name, zi, titles）
- Life_Events（event_id, event_type）
- Time（era, year, month, day）
- Location（dao, fu, zhou, jun, xian, other）
- Official_title（official_title）
- Historical_Events（event_name）
- Passage_Info（doc_id, title, source_type, era）

边类型：
- (Person_Nodes)-[:生平]->(Life_Events)
- (Life_Events)-[:发生于]->(Time)
- (Life_Events)-[:发生于]->(Location)
- (Life_Events)-[:担任]->(Official_title)
- (Person_Nodes)-[:历史事件 {label}]->(Historical_Events)
- (Person_Nodes)-[:在文章中 {level}]->(Passage_Info)
- (Person_Nodes)-[:person_relation {codes, note}]->(Person_Nodes)

请根据用户的统计描述，结合关键词，生成一条只读 Cypher 查询。
只允许 MATCH / OPTIONAL MATCH / CALL / WITH / UNWIND / RETURN，禁止 CREATE、MERGE、DELETE、SET、REMOVE、DROP。

示例：
- "有多少名为张三的人物？" → cypher: "MATCH (n:Person_Nodes {name: $name}) RETURN count(n) AS count", params: {"name": "张三"}
- ""北邙山"作为葬地出现了多少次？" → cypher: "MATCH (e:Life_Events {event_type: '葬'})-[:发生于]->(l:Location) WHERE l.other = $place RETURN count(e) AS count", params: {"place": "北邙山"}

严格返回 JSON，字段：cypher, params, explanation。不输出代码块或任何额外文字。
"""


class CypherGenResult(BaseModel):
    cypher: str
    params: dict
    explanation: str


class GraphStatisticsQueryAtomicSkill(BaseSkill):
    code = "graph_statistics_query_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def _ensure_read_only(self, cypher: str) -> str:
        normalized = " ".join(cypher.strip().split()).upper()
        forbidden_keywords = ["CREATE ", "MERGE ", "DELETE ", "SET ", "REMOVE ", "DROP "]
        if any(keyword in normalized for keyword in forbidden_keywords):
            raise ValueError("只读 Cypher Skill 不允许生成写操作语句。")
        if not normalized.startswith(("MATCH ", "OPTIONAL MATCH ", "CALL ", "WITH ", "UNWIND ", "RETURN ")):
            raise ValueError("生成的 Cypher 不符合只读查询约束。")
        return cypher.strip()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        keyword = str(arguments.get("keyword", "")).strip()
        stat_description = str(arguments.get("stat_description", "")).strip()

        user_prompt = f"关键词：{keyword}\n统计需求：{stat_description}"

        result: CypherGenResult = call_llm_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=CypherGenResult,
            skill_code=self.code,
            additional_metadata=context.metadata,
        )

        try:
            cypher = self._ensure_read_only(result.cypher)
        except ValueError:
            return {
                "error": "生成的查询包含写操作，已拦截",
                "cypher": result.cypher,
                "explanation": result.explanation,
            }

        query_result = self.repository.run_read_query(cypher, result.params)
        return {
            "cypher": cypher,
            "explanation": result.explanation,
            "records": query_result.get("records", []),
            "record_count": query_result.get("record_count", 0),
        }
