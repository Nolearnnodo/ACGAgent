"""查询结果组装回复 Atomic Skill。"""

from pydantic import BaseModel

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import call_llm_structured

_SYSTEM_PROMPT = (
    "你是古籍研究平台的回复组装助手。\n"
    "用户提出了一个查询，系统已取得原始查询结果。\n"
    "请将原始结果整理为清晰、流畅的中文回答，面向人文学者，避免技术术语。\n"
    "只输出 JSON，字段：reply。不要包含 markdown 代码块或任何额外解释。"
)

_SOURCE_NOTES = {
    "llm": "此信息来自模型知识，未经图数据库验证",
    "graph": "以上信息来自图数据库",
}


class _ReplyResult(BaseModel):
    reply: str


class QueryAnswerComposeAtomicSkill(BaseSkill):
    """将原始查询结果转化为人类可读的中文回答。"""

    code = "query_answer_compose_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = str(arguments.get("user_prompt", "")).strip()
        query_type = str(arguments.get("query_type", "")).strip()
        query_result = arguments.get("query_result", "")
        source = str(arguments.get("source", "")).strip()

        compose_prompt = (
            f"用户原始问题：{user_prompt}\n"
            f"查询类型：{query_type}\n"
            f"查询结果：{query_result}"
        )

        result = call_llm_structured(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=compose_prompt,
            schema=_ReplyResult,
            skill_code=self.code,
            additional_metadata=context.metadata,
        )

        note = _SOURCE_NOTES.get(source)
        reply = result.reply + f"\n\n{note}" if note else result.reply

        return {"reply": reply}
