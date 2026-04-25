"""古籍处理 Workflow Skill。"""

from app.agents.context import ExecutionContext
from app.skills.atomic.passage_store_graph import PassageStoreGraphAtomicSkill
from app.skills.atomic.passage_store_sqlite import PassageStoreSqliteAtomicSkill
from app.skills.base import BaseSkill


class PassageIngestionWorkflowSkill(BaseSkill):
    """固定古籍处理流程。"""

    code = "passage_ingestion_workflow"
    allowed_roles = ["admin"]

    def __init__(self) -> None:
        self.sqlite_skill = PassageStoreSqliteAtomicSkill()
        self.graph_skill = PassageStoreGraphAtomicSkill()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:

        step1 = self.sqlite_skill.run(context, arguments)
        context.set_step_result("step2_result", step1)

        step2 = self.graph_skill.run(context, arguments)
        context.set_step_result("step3_result", step2)

        return {
            "reply": "古籍处理流程已完成。",
            "steps": {
                "store_sqlite": step1,
                "store_graph": step2,
            },
        }
