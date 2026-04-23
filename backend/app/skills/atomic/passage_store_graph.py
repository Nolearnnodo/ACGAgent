"""古籍图数据库入库占位 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill


class PassageStoreGraphAtomicSkill(BaseSkill):
    """把 Passage 入图的占位步骤。"""

    code = "passage_store_graph_atomic"
    allowed_roles = ["admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        return self.repository.run_write_query(
            cypher="// 古籍入图占位逻辑，后续由具体 Skill 补齐",
            parameters={
                "doc_id": passage.get("doc_id"),
                "title": passage.get("title"),
            },
        )
