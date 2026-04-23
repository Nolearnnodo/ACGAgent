"""古籍预处理 Atomic Skill。"""

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill


class PassagePreprocessAtomicSkill(BaseSkill):
    """对古籍正文做最小清洗。"""

    code = "passage_preprocess_atomic"
    allowed_roles = ["admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        cleaned_context = str(passage.get("context", "")).strip()
        return {
            "doc_id": passage.get("doc_id"),
            "title": str(passage.get("title", "")).strip(),
            "cleaned_context": cleaned_context,
            "char_count": len(cleaned_context),
        }
