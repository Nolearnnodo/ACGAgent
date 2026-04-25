"""图写入 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill


class GraphWriteAtomicSkill(BaseSkill):
    """图写入最小执行单元。

    该 Skill 仅允许管理员调用。
    """

    code = "graph_write_atomic"
    allowed_roles = ["admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        cypher = str(arguments.get("cypher", "")).strip()
        params = arguments.get("params") or {}

        if not cypher:
            raise ValueError("图写入 Skill 缺少 cypher 参数。")

        return self.repository.run_write_query(
            cypher=cypher,
            parameters=params,
        )
