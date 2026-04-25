"""自然语言转只读 Cypher Atomic Skill。"""

import json

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.llm.providers.factory import get_llm_provider
from app.skills.base import BaseSkill


class NaturalLanguageToCypherReadAtomicSkill(BaseSkill):
    """把自然语言请求转换为只读 Cypher，并执行查询。"""

    code = "nl_to_cypher_read_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.provider = get_llm_provider()
        self.repository = GraphRepository()

    def _ensure_read_only(self, cypher: str) -> str:
        """校验生成的 Cypher 是否属于只读操作。"""

        normalized = " ".join(cypher.strip().split()).upper()
        forbidden_keywords = ["CREATE ", "MERGE ", "DELETE ", "SET ", "REMOVE ", "DROP "]
        if any(keyword in normalized for keyword in forbidden_keywords):
            raise ValueError("只读 Cypher Skill 不允许生成写操作语句。")

        if not normalized.startswith(("MATCH ", "OPTIONAL MATCH ", "CALL ", "WITH ", "UNWIND ", "RETURN ")):
            raise ValueError("生成的 Cypher 不符合只读查询约束。")

        return cypher.strip()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        user_prompt = str(arguments.get("user_prompt", "")).strip()
        if not user_prompt:
            raise ValueError("自然语言转 Cypher Skill 缺少 user_prompt。")

        messages = [
            {
                "role": "system",
                "content": (
                    "你是 Neo4j Cypher 只读查询生成器。"
                    "请根据用户请求返回严格 JSON，字段包括：cypher, params, explanation。"
                    "只允许生成只读查询，禁止任何 CREATE、MERGE、DELETE、SET、REMOVE、DROP。"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]

        llm_content = self.provider.chat_completion(messages, metadata={"skill_code": self.code})
        parsed = json.loads(llm_content)
        cypher = self._ensure_read_only(str(parsed.get("cypher", "")))
        params = parsed.get("params") or {}

        query_result = self.repository.run_read_query(cypher=cypher, parameters=params)
        return {
            "cypher": cypher,
            "params": params,
            "explanation": parsed.get("explanation", ""),
            "query_result": query_result,
        }
