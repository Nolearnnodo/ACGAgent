"""图查询 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill


class GraphQueryAtomicSkill(BaseSkill):
    """图查询最小执行单元。"""

    code = "graph_query_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params") or {}

        if not cypher:
            raise ValueError("图查询 Skill 缺少 cypher 参数。")

        return self.repository.run_read_query(
            cypher=cypher,
            parameters=params,
        )
