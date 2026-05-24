"""查询意图分类 Atomic Skill。"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured

_SYSTEM_PROMPT = (
    "你是古籍研究平台的查询意图分类器。\n"
    "请按以下步骤推理后输出严格 JSON：\n"
    "1. 判断用户请求属于哪类查询：\n"
    "   - person_info：查询某人的基本信息、生平、著作等；\n"
    "   - person_relation：查询两人之间的关系或某人的社会关系网络；\n"
    "   - graph_statistics：查询图数据库的统计信息，如节点数、边数、最活跃节点等。\n"
    "2. 从请求中提取所需参数（人名、关键词等）。\n"
    "3. 输出 JSON，字段：query_type, extracted_params, reasoning。\n"
    "extracted_params 可包含：person_name, person_a, person_b, keyword, stat_description。\n"
    "只输出 JSON，不要包含 markdown 代码块或任何额外解释。"
)


class _ExtractedParams(BaseModel):
    person_name: Optional[str] = None
    person_a: Optional[str] = None
    person_b: Optional[str] = None
    keyword: Optional[str] = None
    stat_description: Optional[str] = None


class _IntentResult(BaseModel):
    query_type: Literal["person_info", "person_relation", "graph_statistics"]
    extracted_params: _ExtractedParams
    reasoning: str


class QueryIntentClassifierAtomicSkill(BaseSkill):
    """对用户输入进行查询意图分类，并提取结构化参数。"""

    code = "query_intent_classifier_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = str(arguments.get("user_prompt", "")).strip()
        recent_messages = arguments.get("recent_messages") or []

        context_lines = []
        for msg in recent_messages[-4:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in {"user", "assistant"} and content:
                context_lines.append(f"[{role}] {content}")

        full_prompt = user_prompt
        if context_lines:
            full_prompt = "近期对话：\n" + "\n".join(context_lines) + "\n\n当前请求：" + user_prompt

        try:
            result = call_llm_structured(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=full_prompt,
                schema=_IntentResult,
                skill_code=self.code,
                additional_metadata=context.metadata,
            )
        except LLMStructuredError:
            return {
                "query_type": "person_info",
                "extracted_params": {"person_name": user_prompt},
                "reasoning": "意图分类失败，回退到默认 person_info 查询。",
            }

        return {
            "query_type": result.query_type,
            "extracted_params": result.extracted_params.model_dump(exclude_none=True),
            "reasoning": result.reasoning,
        }
