"""Skill-4 + Skill-5 + Skill-7 合并：事件 + 历史关联 + 关系字母码。

仅对 level∈{1,2} 的人物展开。

Skill-4: 提取出生/籍贯/死亡/埋葬/任职 life_events
        - 籍贯事件不建 Time
        - era 必填，year/month/day 可空
        - 检测 Time 与历史事件区间重叠时建 Historical_Events→Time 边
        - 任职额外提 official_title
        - 仅写文中明示的地点；全空跳过 Location
Skill-5: 人物 ↔ 历史事件 关联，relation_label ≤15 字
Skill-7: 人物 ↔ 人物 字母关系码 (≤3 字母)，双向写入
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.services.dictionary_service import (
    find_overlapping_events,
    get_historical_event_set,
    get_relation_code_set,
    is_valid_relation_chain,
    match_era,
)
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured

EventType = Literal["出生", "籍贯", "死亡", "埋葬", "任职"]


class TimeBlock(BaseModel):
    era: str
    year: int | None = None
    month: int | None = None
    day: int | None = None


class LocationBlock(BaseModel):
    dao: str | None = None
    fu: str | None = None
    zhou: str | None = None
    jun: str | None = None
    xian: str | None = None
    other: str | None = None


class LifeEventOut(BaseModel):
    event_type: EventType
    time: TimeBlock | None = None
    location: LocationBlock | None = None
    official_title: str | None = None
    evidence: str = ""

    @field_validator("event_type")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip()


class HistoricEventLink(BaseModel):
    event_name: str
    relation_label: str = Field(..., max_length=30)
    evidence: str = ""


class PersonRelationOut(BaseModel):
    src_person_id: int
    tgt_person_id: int
    codes: list[str]
    note: str | None = None
    evidence: str = ""


class PersonEvents(BaseModel):
    person_id: int
    life_events: list[LifeEventOut] = Field(default_factory=list)
    historic_events: list[HistoricEventLink] = Field(default_factory=list)


class EventRelationOutput(BaseModel):
    person_events: list[PersonEvents]
    person_relations: list[PersonRelationOut] = Field(default_factory=list)


def _build_system_prompt(persons_summary: list[dict]) -> str:
    persons_lines = [
        f"  - person_id={p['person_id']} sequence={p['sequence']} "
        f"name={p['name']} level={p['level']}"
        for p in persons_summary
    ]
    return f"""你是历史事件与人物关系抽取专家。仅对 level∈{{1,2}} 的人物展开抽取。

【已确定的人物】
{chr(10).join(persons_lines)}

【输出 JSON 严格结构】
{{
  "person_events": [
    {{
      "person_id": <int>,
      "life_events": [
        {{
          "event_type": "出生" | "籍贯" | "死亡" | "埋葬" | "任职",
          "time": {{"era": "<年号>", "year": <int|null>, "month": <int|null>, "day": <int|null>}} 或 null,
          "location": {{"dao": ..., "fu": ..., "zhou": ..., "jun": ..., "xian": ..., "other": ...}} 或 null,
          "official_title": "<仅任职时填; 否则 null>",
          "evidence": "<原文≤30字片段>"
        }}
      ],
      "historic_events": [
        {{"event_name": "<必须落在历史事件字典>", "relation_label": "<≤15字>", "evidence": "<原文≤30字片段>"}}
      ]
    }}
  ],
  "person_relations": [
    {{
      "src_person_id": <int>, "tgt_person_id": <int>,
      "codes": ["F"|"M"|"S"|"D"|"H"|"W"|"Z"|"C"|"B"|"O"],
      "note": "<仅 codes=['O'] 时填，≤15 字>",
      "evidence": "<原文≤30字>"
    }}
  ]
}}

【字段约束】
- event_type 仅 5 选 1：出生/籍贯/死亡/埋葬/任职。出生/籍贯/死亡/埋葬 每人至多一条；任职可多条。
- 籍贯事件 time 必为 null（与图数据库 schema 对齐）。
- 任职事件必须填 official_title；其他事件 official_title 必为 null。
- time.era 必填且必须落在年号字典内；年/月/日缺失填 null。
- location 五级地名 + other：原文未明示的填 null；全部为 null 时整个 location 字段填 null。
- historic_events.event_name 必须落在历史事件字典内（不在的让我处理）。
- person_relations.codes 字母序列长度 ≤ 3；超过 3 字母时强制写 ["O"] + note≤15字。
  关系字母编码：F=父 M=母 S=子 D=女 H=夫 W=妻 Z=妾 C=非直系兄弟姐妹 B=直系兄弟姐妹 O=其他。
- person_relations 只列单向，系统会自动补反向。

不输出任何解释或代码块，仅输出 JSON。
"""


class EventRelationAtomicSkill(BaseSkill):
    """Skill-4 + 5 + 7：事件 + 历史关联 + 字母码。"""

    code = "event_relation_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        text = passage.get("context", "")
        person_layer = context.metadata.get("person_layer", {})
        warnings = context.metadata.setdefault("warnings", [])

        # 仅取 level ∈ {1, 2} 的人物供 LLM 处理
        eligible_persons = [
            p for p in person_layer.get("persons", []) if int(p["level"]) in (1, 2)
        ]
        if not eligible_persons:
            warnings.append(
                {"type": "no_eligible_person", "detail": "无 level∈{1,2} 人物可展开事件抽取"}
            )
            output = {"person_events": [], "person_relations": []}
            context.metadata["event_relation"] = output
            return output

        persons_summary = [
            {
                "person_id": p["person_id"],
                "sequence": p["sequence"],
                "name": p["name"],
                "level": p["level"],
            }
            for p in eligible_persons
        ]

        # 把 pair_relations 候选作为参考喂给 LLM
        pair_hints = json.dumps(
            person_layer.get("pair_relations", []), ensure_ascii=False
        )

        try:
            result = call_llm_structured(
                system_prompt=_build_system_prompt(persons_summary),
                user_prompt=(
                    f"【全文】\n{text}\n\n【人物两两关系候选(描述性)】\n{pair_hints}"
                ),
                schema=EventRelationOutput,
                skill_code=self.code,
                additional_metadata=context.metadata,
            )
            person_events = [pe.model_dump() for pe in result.person_events]
            person_relations = [pr.model_dump() for pr in result.person_relations]
        except LLMStructuredError as exc:
            warnings.append({"type": "event_relation_fallback", "detail": str(exc)})
            person_events = []
            person_relations = []

        # ===== 校验 + 入图 =====
        repo = GraphRepository()
        valid_event_names = get_historical_event_set()
        valid_codes = get_relation_code_set()

        # ---- Skill-4：life_events 入图 ----
        for pe in person_events:
            person_id = int(pe["person_id"])
            for idx, ev in enumerate(pe.get("life_events") or []):
                event_type = ev.get("event_type")
                time = ev.get("time")
                location = ev.get("location")
                official_title = ev.get("official_title")

                # 籍贯不带 time 校验
                if event_type == "籍贯" and time is not None:
                    warnings.append(
                        {
                            "type": "schema_violation",
                            "detail": f"person {person_id} 籍贯事件不应带 time，已忽略 time",
                        }
                    )
                    time = None

                # 任职以外不应有 official_title
                if event_type != "任职" and official_title:
                    warnings.append(
                        {
                            "type": "schema_violation",
                            "detail": f"person {person_id} 非任职事件不应有 official_title，已忽略",
                        }
                    )
                    official_title = None

                # era 字典校验 + dynasty 推断（可缺）
                if time and time.get("era"):
                    entry = match_era(time["era"])
                    if entry is None:
                        warnings.append(
                            {
                                "type": "unmatched_era",
                                "detail": f"person {person_id} 事件 era={time['era']} 不在字典",
                            }
                        )

                event_id = f"le_{person_id}_{idx + 1}"
                try:
                    repo.upsert_life_event(
                        person_id=person_id,
                        event_id=event_id,
                        event_type=event_type,
                        time=time,
                        location=location,
                        official_title=official_title,
                    )
                except Exception as exc:
                    warnings.append(
                        {
                            "type": "neo4j_write_failed",
                            "detail": f"upsert_life_event {event_id}: {exc}",
                        }
                    )

                # ---- Skill-4 第 3 条：Time 与历史事件区间重叠则建边 ----
                if time and time.get("year"):
                    try:
                        for he in find_overlapping_events(int(time["year"])):
                            repo.upsert_historical_event_time_link(
                                event_name=he.event_name, time=time
                            )
                    except Exception as exc:
                        warnings.append(
                            {
                                "type": "neo4j_write_failed",
                                "detail": f"upsert_historical_event_time_link: {exc}",
                            }
                        )

            # ---- Skill-5：人物 ↔ 历史事件 ----
            for he_link in pe.get("historic_events") or []:
                event_name = he_link.get("event_name")
                if event_name not in valid_event_names:
                    warnings.append(
                        {
                            "type": "unmatched_historic_event",
                            "detail": f"person {person_id} historic_events.event_name={event_name} 不在字典",
                        }
                    )
                    continue
                try:
                    repo.upsert_person_historical_event(
                        person_id=person_id,
                        event_name=event_name,
                        relation_label=(he_link.get("relation_label") or "")[:15],
                    )
                except Exception as exc:
                    warnings.append(
                        {
                            "type": "neo4j_write_failed",
                            "detail": f"upsert_person_historical_event: {exc}",
                        }
                    )

        # ---- Skill-7：人物 ↔ 人物（双向） ----
        for rel in person_relations:
            codes = rel.get("codes") or []
            # 长度上限 3，超出强制改 ["O"] + note
            if len(codes) > 3 or any(c not in valid_codes for c in codes):
                warnings.append(
                    {
                        "type": "relation_chain_invalid",
                        "detail": f"原 codes={codes} 不合法，已降级为 ['O']",
                    }
                )
                codes = ["O"]
                if not rel.get("note"):
                    rel["note"] = "复合关系"

            if not is_valid_relation_chain(codes):
                continue

            src = int(rel["src_person_id"])
            tgt = int(rel["tgt_person_id"])
            note = rel.get("note")
            try:
                repo.upsert_person_relation(
                    source_person_id=src,
                    target_person_id=tgt,
                    codes=codes,
                    note=note,
                )
                # 反向：纯 ["O"] 直接镜像；亲属编码可由调用方人工核对，
                # 此处先按字母级镜像（满足图遍历需要，正确性 review 由专家）
                repo.upsert_person_relation(
                    source_person_id=tgt,
                    target_person_id=src,
                    codes=_inverse_codes(codes),
                    note=note,
                )
            except Exception as exc:
                warnings.append(
                    {
                        "type": "neo4j_write_failed",
                        "detail": f"upsert_person_relation: {exc}",
                    }
                )

        output = {"person_events": person_events, "person_relations": person_relations}
        context.metadata["event_relation"] = output
        return output


_INVERSE_MAP = {
    "F": "S",
    "M": "S",  # 反向「子」与「女」无法仅从字母推断性别，统一回填 S
    "S": "F",  # 同理
    "D": "F",
    "H": "W",
    "W": "H",
    "Z": "H",
    "C": "C",
    "B": "B",
    "O": "O",
}


def _inverse_codes(codes: list[str]) -> list[str]:
    """字母关系链反向。仅做字母层翻转，不区分性别（图层细化由专家校对）。"""

    return [_INVERSE_MAP.get(c, c) for c in reversed(codes)]
