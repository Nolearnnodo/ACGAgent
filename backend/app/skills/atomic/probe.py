"""Skill-0：前置探针扫描。

输出 4 个布尔标签写入 ExecutionContext.metadata["probe"]，
供下游 Skill 装配 prompt（女性挂"夫家优先"、残碑放宽生卒年等）使用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.context import ExecutionContext
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured


class ProbeOutput(BaseModel):
    is_female: bool = Field(default=False, description="墓主是否为女性")
    is_damaged: bool = Field(default=False, description="正文是否含 □ 残缺")
    is_clergy: bool = Field(default=False, description="墓主是否为出家人(僧/道/尼)")
    has_courtesy_name: bool = Field(default=False, description="是否明确出现'字某某'")


SYSTEM_PROMPT = """你是古籍属性扫描仪。请快速浏览给定的古文片段（主要看标题与开头），
仅输出 4 个布尔标签，严格 JSON 格式：
{"is_female": false, "is_damaged": false, "is_clergy": false, "has_courtesy_name": false}

判定要点：
- is_female：标题或开头出现"夫人/妻/氏/比丘尼/女"等女性专属字样。
- is_damaged：正文含 □ 等残缺字符。
- is_clergy：墓主是僧/道/尼/比丘/比丘尼/法师/上人/禅师等。
- has_courtesy_name：明确出现"字某某"或"号某某"。

不输出任何解释或代码块。
"""


class ProbeAtomicSkill(BaseSkill):
    """Skill-0：探针扫描。"""

    code = "probe_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        title = passage.get("title", "")
        text = (passage.get("context") or "")[:500]

        # is_damaged 可以本地直接判定，无需 LLM；同时给 LLM 校验
        local_is_damaged = "□" in (passage.get("context") or "")

        try:
            result = call_llm_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=f"【文件名】{title}\n\n【文献片段】\n{text}",
                schema=ProbeOutput,
                skill_code=self.code,
            )
            output = result.model_dump()
        except LLMStructuredError as exc:
            # 兜底：4 项默认 false，仅 is_damaged 用本地判定。
            output = ProbeOutput(is_damaged=local_is_damaged).model_dump()
            context.metadata.setdefault("warnings", []).append(
                {"type": "probe_fallback", "detail": str(exc)}
            )

        # is_damaged 总以本地实际为准
        output["is_damaged"] = local_is_damaged
        context.metadata["probe"] = output
        return output
