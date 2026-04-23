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
        user_prompt = arguments.get("user_prompt", "")
        return self.repository.run_read_query(
            cypher="// 占位查询，后续由具体 Skill 生成 Cypher",
            parameters={"user_prompt": user_prompt},
        )
