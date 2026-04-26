"""Skill-1：抽取文章基础信息。

输入：passage.title + passage.context 头部 + 探针标签 + 字典提示
输出：source_type_code (0/1) + era + dynasty
副作用：
  - SQLite UPDATE passages SET source_type_code, era WHERE doc_id
  - Neo4j MERGE Passage_Info {doc_id, title, source_type, era}
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.agents.context import ExecutionContext
from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.models.passage import Passage
from app.services.dictionary_service import (
    get_era_entries,
    get_era_set,
    get_source_types,
    match_era,
)
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured


class PassageMetaOutput(BaseModel):
    source_type_code: Literal[0, 1] = Field(
        ..., description="0=墓志铭/塔铭, 1=一般历史文献"
    )
    era: str = Field(..., description="文献创作的年号关键字（必须落在字典内）")
    dynasty: str = Field(..., description="对应朝代（用于 era 跨朝代消歧）")

    @field_validator("era")
    @classmethod
    def _strip_era(cls, v: str) -> str:
        return v.strip()


def _build_system_prompt() -> str:
    eras_grouped: dict[str, list[str]] = {}
    for entry in get_era_entries():
        eras_grouped.setdefault(entry.dynasty, []).append(entry.era)
    eras_hint_lines = [
        f"  {dyn}: {', '.join(sorted(set(items)))}"
        for dyn, items in sorted(eras_grouped.items())
    ]
    return f"""你是古籍信息抽取专家。请根据给定文章的标题与正文片段，输出严格 JSON：
- source_type_code: 0=墓志铭/塔铭(一手资料), 1=一般历史文献(正史/方志/笔记)
- era: 文献内容主要时段对应的年号（必须严格落在字典内，简体形式）
- dynasty: era 所属朝代（用于跨朝代重名年号消歧，如「中兴」「至德」等）

合法 source_types: {json.dumps(get_source_types(), ensure_ascii=False)}

合法年号字典（按朝代列示）：
{chr(10).join(eras_hint_lines)}

注意：
1. 上元在唐朝出现两次，请按上下文选「上元_G」(高宗 674-676) 或「上元_S」(肃宗 760-761)。
2. 输出严格 JSON，不输出任何解释或代码块。
"""


class PassageMetaAtomicSkill(BaseSkill):
    """Skill-1：文章基础信息抽取。"""

    code = "passage_meta_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        doc_id = passage["doc_id"]
        title = passage.get("title", "")
        text = passage.get("context", "")
        head = text[:1500]

        eras = get_era_set()
        warnings = context.metadata.setdefault("warnings", [])

        try:
            result = call_llm_structured(
                system_prompt=_build_system_prompt(),
                user_prompt=f"【标题】{title}\n\n【正文片段】\n{head}",
                schema=PassageMetaOutput,
                skill_code=self.code,
            )
            output = result.model_dump()
        except LLMStructuredError as exc:
            warnings.append(
                {"type": "passage_meta_fallback", "detail": str(exc)}
            )
            # 兜底：从标题猜 source_type；era 留 None 给后续 Skill 兜兜
            guessed_code = 0 if any(k in title for k in ("墓誌", "墓志", "塔銘", "塔铭")) else 1
            output = {
                "source_type_code": guessed_code,
                "era": "",
                "dynasty": "",
            }

        # 校验 era 是否在字典内
        era_value: str | None = output.get("era") or None
        dynasty_value: str | None = output.get("dynasty") or None
        if era_value and era_value not in eras:
            warnings.append(
                {"type": "unmatched_era", "detail": f"era={era_value} 不在字典中"}
            )
        elif era_value and dynasty_value:
            entry = match_era(era_value, dynasty=dynasty_value)
            if entry is None:
                warnings.append(
                    {
                        "type": "ambiguous_era",
                        "detail": f"era={era_value} 在 dynasty={dynasty_value} 下无法唯一定位",
                    }
                )

        # 写回 SQLite passages
        with SessionLocal() as db:
            row = db.get(Passage, doc_id)
            if row is not None:
                row.source_type_code = int(output["source_type_code"])
                row.era = era_value
                db.add(row)
                db.commit()

        # 写 Neo4j Passage_Info
        try:
            GraphRepository().upsert_passage_info(
                doc_id=doc_id,
                title=title,
                source_type=int(output["source_type_code"]),
                era=era_value,
            )
        except Exception as exc:
            warnings.append(
                {"type": "neo4j_write_failed", "detail": f"upsert_passage_info: {exc}"}
            )

        context.metadata["passage_meta"] = output
        return output
