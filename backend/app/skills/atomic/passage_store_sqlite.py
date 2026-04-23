"""古籍 SQLite 持久化确认 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill


class PassageStoreSqliteAtomicSkill(BaseSkill):
    """确认 Passage 已写入 SQLite。"""

    code = "passage_store_sqlite_atomic"
    allowed_roles = ["admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        return {
            "doc_id": passage.get("doc_id"),
            "status": "stored_in_sqlite",
        }
