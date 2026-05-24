"""Skill-2 + Skill-3 + Skill-6 合并：人物层抽取。

输入：passage.title + passage.context + 探针 + Stage 1 输出
输出：
  - persons[]: {person_id, name, zi, titles, level∈{1,2,3}, sequence}
  - pair_relations[]: {src_person_id, tgt_person_id, descriptive_text}
副作用：
  - 校验 level=1 唯一
  - person_id = doc_id * 1000 + sequence (系统侧合成，避免 LLM 拼错)
  - Neo4j MERGE Person_Nodes × N + (Person)-[:在文章中 {level}]->(Passage_Info)
  - pair_relations 仅写入 step_runs.output_json，供 Stage 3 (event_relation) 读取
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured


class PersonItem(BaseModel):
    sequence: int = Field(..., ge=1, description="文章内人物出现序号，1 起")
    name: str = Field(..., min_length=1)
    zi: str | None = None
    titles: list[str] = Field(default_factory=list)
    level: int = Field(..., description="1=主人公 2=核心 3=边缘")

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: int) -> int:
        if v not in (1, 2, 3):
            raise ValueError("level 必须为 1/2/3")
        return v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


class PairRelation(BaseModel):
    src_sequence: int = Field(..., ge=1)
    tgt_sequence: int = Field(..., ge=1)
    descriptive_text: str = Field(..., max_length=200)


class PersonLayerOutput(BaseModel):
    persons: list[PersonItem]
    pair_relations: list[PairRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_level1(self) -> "PersonLayerOutput":
        l1 = [p for p in self.persons if p.level == 1]
        if len(l1) != 1:
            raise ValueError(f"必须有且仅有 1 位 level=1 主人公，实际 {len(l1)}")
        # sequence 不重
        seqs = [p.sequence for p in self.persons]
        if len(seqs) != len(set(seqs)):
            raise ValueError("persons.sequence 必须全文章内唯一")
        return self


def _build_system_prompt(probe: dict) -> str:
    hints = []
    if probe.get("is_female"):
        hints.append("- 女性专属：建议优先抽夫家与原生家族成员。")
    if probe.get("is_damaged"):
        hints.append("- 含残缺字 □：可放宽，对实在残缺的人物字段填空字符串。")
    if probe.get("is_clergy"):
        hints.append("- 墓主为出家人：法名/号也作为 titles；师承关系作为重要人物。")
    if probe.get("has_courtesy_name"):
        hints.append("- 文中出现「字某某」：必须把字剥离到 zi 字段，不可混入 name。")
    hints_block = "\n".join(hints) if hints else "(无)"
    return f"""你是历史人物抽取专家，负责从给定古文中抽取所有出现的人物，并对每位人物做重要度评级。

【探针提示】
{hints_block}

【输出 JSON 严格结构】
{{
  "persons": [
    {{"sequence": 1, "name": "...", "zi": "...或null", "titles": ["..."], "level": 1}},
    ...
  ],
  "pair_relations": [
    {{"src_sequence": 1, "tgt_sequence": 2, "descriptive_text": "原文中描述两人关系的≤200字片段"}},
    ...
  ]
}}

【字段约束】
- sequence: 文章内人物出现序号，从 1 起递增。每位人物唯一。
- name: 纯本名或「姓+名」。绝不允许包含封号/尊称(如"府君"、"夫人")。
- zi: 「字某某」或「号某某」拆出后的字号；无则填 null。
- titles: 称号/封号/谥号/法号 列表，可空。
- level:
    1 = 文章主人公（每篇有且仅有 1 位）
    2 = 核心人物：主人公的直系亲属、夫妻、师承或重要关联
    3 = 边缘人物：仅一过性提及、缺乏实质性描述
- pair_relations: 列出文中明确描述了关系的两两人物对（包括 level=3）。
  descriptive_text 直接引原文片段，不要做关系编码（编码留给后续 Skill）。

【绝对禁止】
- 把"先考"、"亡妻"、"恩师"、"友人"等没有具体姓名的泛称作为 person 抽出
- 把 level=1 同时给多个人

不输出任何解释或代码块，仅输出 JSON。
"""


class PersonLayerAtomicSkill(BaseSkill):
    """Skill-2 + 3 + 6：人物层抽取。"""

    code = "person_layer_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        doc_id = passage["doc_id"]
        title = passage.get("title", "")
        text = passage.get("context", "")
        probe = context.metadata.get("probe", {})
        warnings = context.metadata.setdefault("warnings", [])

        try:
            result = call_llm_structured(
                system_prompt=_build_system_prompt(probe),
                user_prompt=f"【标题】{title}\n\n【全文】\n{text}",
                schema=PersonLayerOutput,
                skill_code=self.code,
                additional_metadata=context.metadata,
            )
            persons_raw = [p.model_dump() for p in result.persons]
            pairs_raw = [r.model_dump() for r in result.pair_relations]
        except LLMStructuredError as exc:
            warnings.append({"type": "person_layer_fallback", "detail": str(exc)})
            persons_raw = [
                {
                    "sequence": 1,
                    "name": title or "未知",
                    "zi": None,
                    "titles": [],
                    "level": 1,
                }
            ]
            pairs_raw = []

        # 系统侧合成 person_id（不让 LLM 自己拼）
        for p in persons_raw:
            p["person_id"] = doc_id * 1000 + int(p["sequence"])

        # 构建 sequence -> person_id 映射，用于把 pair_relations 的 sequence 转成 id
        seq_to_id = {p["sequence"]: p["person_id"] for p in persons_raw}
        pair_relations: list[dict] = []
        for r in pairs_raw:
            src_id = seq_to_id.get(int(r["src_sequence"]))
            tgt_id = seq_to_id.get(int(r["tgt_sequence"]))
            if src_id is None or tgt_id is None:
                warnings.append(
                    {
                        "type": "pair_relation_orphan",
                        "detail": f"pair_relation 引用未知 sequence: {r}",
                    }
                )
                continue
            pair_relations.append(
                {
                    "src_person_id": src_id,
                    "tgt_person_id": tgt_id,
                    "descriptive_text": r["descriptive_text"],
                }
            )

        # Neo4j 写入：所有 level（含 3）都建节点，与文章建 :在文章中 边
        repo = GraphRepository()
        for p in persons_raw:
            try:
                repo.upsert_person(
                    person_id=p["person_id"],
                    name=p["name"],
                    zi=p.get("zi"),
                    titles=p.get("titles") or [],
                    level=int(p["level"]),
                    passage_doc_id=doc_id,
                )
            except Exception as exc:
                warnings.append(
                    {
                        "type": "neo4j_write_failed",
                        "detail": f"upsert_person {p['person_id']}: {exc}",
                    }
                )

        output = {"persons": persons_raw, "pair_relations": pair_relations}
        context.metadata["person_layer"] = output
        return output
