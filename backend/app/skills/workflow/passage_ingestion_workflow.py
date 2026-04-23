"""古籍处理 Workflow Skill。"""

from app.agents.context import ExecutionContext
from app.skills.atomic.passage_preprocess import PassagePreprocessAtomicSkill
from app.skills.atomic.passage_store_graph import PassageStoreGraphAtomicSkill
from app.skills.atomic.passage_store_sqlite import PassageStoreSqliteAtomicSkill
from app.skills.base import BaseSkill


class PassageIngestionWorkflowSkill(BaseSkill):
    """固定古籍处理流程。"""

    code = "passage_ingestion_workflow"
    allowed_roles = ["admin"]

    def __init__(self) -> None:
        self.preprocess_skill = PassagePreprocessAtomicSkill()
        self.sqlite_skill = PassageStoreSqliteAtomicSkill()
        self.graph_skill = PassageStoreGraphAtomicSkill()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        step1 = self.preprocess_skill.run(context, arguments)
        context.set_step_result("step1_result", step1)

        step2 = self.sqlite_skill.run(context, arguments)
        context.set_step_result("step2_result", step2)

        step3 = self.graph_skill.run(context, arguments)
        context.set_step_result("step3_result", step3)

        return {
            "reply": "古籍处理流程已完成。",
            "steps": {
                "preprocess": step1,
                "store_sqlite": step2,
                "store_graph": step3,
            },
        }
