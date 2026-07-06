"""同名人物 AI 考据报告生成与读取。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db import base as _model_registry  # noqa: F401  确保所有 ORM 模型已注册
from app.graph.repository import GraphRepository
from app.llm.providers.factory import get_llm_provider
from app.models.annotation import IdentityAIReport
from app.models.passage import Passage
from app.observability.llm_tracer import traced_chat_completion

ReportGenerator = Callable[[dict[str, Any]], str]


IDENTITY_REPORT_SYSTEM_PROMPT = """# Role: 唐代文献考据与知识图谱分析专家

## Profile
你是一位精通唐代历史、史书墓志铭研究以及知识图谱逻辑推理的专家。你擅长进行“历史人物实体消歧”，能够通过比对古代文献残卷与多跳知识图谱，精准判断不同史料中出现的同名人物是否为同一个人，并给出严谨的考据逻辑。

## 数据特征说明
用户将提供两篇唐代墓志铭文献，以及以该同名人物为中心的两份多跳知识图谱。

**提示：知识图谱来自 Neo4j，可能包含但不限于以下节点、关系和结构化信息：**

### 1. 核心节点类型
- **Passage_Info**：文献节点，表示墓志铭或其他来源文本，可能包含 `doc_id`、`title`、`source_type`、`era` 等信息。
- **Person_Nodes**：人物节点，表示文献中抽取出的人物，可能包含 `person_id`、`name`、`zi`、`titles`、`merged_person_ids` 等信息。
- **Life_Events**：生平事件节点，表示人物的出生、籍贯、死亡、埋葬、任职等事件，常见字段包括 `event_id`、`event_type`。
- **Time**：时间节点，通常以年号和年份组织，可能包含 `era`、`year`、`month`、`day`。
- **Location**：地点节点，可能包含 `dao`、`fu`、`zhou`、`jun`、`xian`、`other` 等层级地名。
- **Official_title**：官职节点，表示任职事件中的官职名称。
- **Historical_Events**：历史事件节点，表示人物或关联人物参与、经历或被关联到的历史大事件，可能包含 `event_name`。

### 2. 关键关系类型
- **Person_Nodes -[在文章中 {level}]-> Passage_Info**：人物出现在某篇文献中，`level=1` 通常表示该文核心人物，`level=2` 表示重要关联人物，`level=3` 表示一般人物。
- **Person_Nodes -[生平]-> Life_Events**：人物拥有某项生平事件。
- **Life_Events -[发生于]-> Time / Location**：生平事件发生的时间或地点。
- **Life_Events -[担任]-> Official_title**：任职类生平事件对应的官职。
- **Person_Nodes -[历史事件]-> Historical_Events**：人物与某历史事件存在关联。
- **Historical_Events -[发生于]-> Time**：历史事件对应的时间。
- **Person_Nodes -[person_relation]-> Person_Nodes**：人物之间的亲属或社会关系，关系字段可能包含 `codes`、`note`、`evidence`、`source_doc_id`、`direction_verified`。

### 3. person_relation 关系编码说明
`person_relation.codes` 使用字母序列表示人物关系：F=父，M=母，S=子，D=女，H=夫，W=妻，Z=妾，C=非直系兄弟姐妹，B=直系兄弟姐妹，O=其他关系。字母序列可表示多跳亲属关系。若关系过长或无法精确编码，可能使用 `O` 并配合 `note` 说明。

### 4. 多跳图谱证据的理解方式
知识图谱可能不仅包含目标人物本人的直接信息，也可能包含通过亲属、姻亲、同族、任官、地望、历史事件等路径扩展出的多跳证据。分析时应特别关注亲属与家族网络、文献来源与人物等级、生平事件链、时空坐标、官职经历、历史事件关联和多跳关系路径。

## 目标
根据输入的“目标人物”名称、两份文献及两份多跳知识图谱，综合研判这两篇文章中的同名人物是否为同一人。你需要给出明确结论、**同人置信度评估（0-10分）**，并清晰地阐述你的判断依据。

## 输出结构要求
请结合文献与图谱，进行深度交叉考据，并输出包含以下模块的分析报告：

### 结论与置信度
- **研判结论**：【是同一人】 / 【不是同一人】 / 【证据不足，存疑】
- **同人置信度**：**[填入 0 到 10 的数字] / 10**
  - 0 = 非常确定不是同一人
  - 5 = 证据不足、难以判断或正反证据大致相当
  - 10 = 非常确定是同一人

### 判断依据
*(请不拘泥于固定步骤，自由展开你的考据逻辑，但必须涵盖以下核心维度的分析：)*

#### 1. 核心特征与图谱多跳比对
综合运用文献原文与图谱中的多跳关系，对比两个人物在“亲属、官职、生卒时空、籍贯葬地、文献来源、生平事件、历史事件”等方面的吻合度或冲突点。

#### 2. 证据链推演与关键锚点
详细说明你是如何构建证据链的。请特别指出支撑你结论的“强锚点”，以及导致“不是同一人”结论的“硬性冲突”。

#### 3. 疑点辨析与史学解释
如果两份史料间存在部分信息缺失或表面上的不一致，请尝试运用唐代历史背景（如：墓志铭常见的“隐恶扬善”、避讳、赐姓、过继、贬谪、官职追赠、葬地迁移、家族称谓省略等）进行史学视角的合理化解释。

## 限制要求
1. **严禁幻觉**：判断必须严格基于用户提供的文献和图谱数据。
2. 不得捏造历史事实或脑补图谱中不存在的关系。
3. **交叉验证**：不可单一只看文献或只看图谱，必须将两者的节点、关系、原文证据相互印证。
4. **缺失证据不等于反证**：如果一方图谱信息较少，不能仅因缺失就判定为不同人；只有存在明确冲突时，才可作为“不是同一人”的依据。
5. **图谱字段优先按结构理解**：遇到 `Life_Events`、`Time`、`Location`、`Official_title`、`Historical_Events`、`person_relation.codes` 等字段时，应按上述 schema 解释，不要按普通文本自由发挥。
6. **置信度方向约束**：置信度表示“同人可能性”而非“判断把握程度”。证据不足时应接近 5 分；只有存在明确反证时才接近 0 分，只有存在明确强锚点时才接近 10 分。
"""


def normalize_pair(source_person_id: int, target_person_id: int) -> tuple[int, int]:
    return (
        (source_person_id, target_person_id)
        if source_person_id <= target_person_id
        else (target_person_id, source_person_id)
    )


def get_identity_report_for_pair(
    db: Session,
    source_person_id: int,
    target_person_id: int,
) -> IdentityAIReport | None:
    source_id, target_id = normalize_pair(source_person_id, target_person_id)
    return (
        db.query(IdentityAIReport)
        .filter(
            IdentityAIReport.source_person_id == source_id,
            IdentityAIReport.target_person_id == target_id,
        )
        .first()
    )


def generate_all_same_name_identity_reports(
    db: Session,
    repo: GraphRepository | None = None,
    generate_report: ReportGenerator | None = None,
    generated_by_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    repo = repo or GraphRepository()
    generate_report = generate_report or call_identity_report_llm
    records = repo.list_all_same_name_pairs().get("records", [])
    existing_reports = _load_existing_reports(db, records)
    result: dict[str, Any] = {
        "pair_count": len(records),
        "created_count": 0,
        "updated_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "failures": [],
    }

    for record in records:
        source_id = int(record["source_person_id"])
        target_id = int(record["target_person_id"])
        pair = normalize_pair(source_id, target_id)
        existing = existing_reports.get(pair)
        if existing is not None and not force:
            result["skipped_count"] += 1
            continue

        try:
            payload = build_identity_report_payload(db, repo, record)
            report_markdown = generate_report(payload).strip()
            if not report_markdown:
                raise ValueError("LLM 返回空报告。")

            source_name, target_name = _normalized_names(record)
            now = datetime.now(timezone.utc)
            created = existing is None
            if existing is None:
                db.add(
                    IdentityAIReport(
                        source_person_id=pair[0],
                        target_person_id=pair[1],
                        source_name=source_name,
                        target_name=target_name,
                        report_markdown=report_markdown,
                        generated_by_id=generated_by_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.source_name = source_name
                existing.target_name = target_name
                existing.report_markdown = report_markdown
                existing.generated_by_id = generated_by_id
                existing.updated_at = now
            db.commit()
            if created:
                result["created_count"] += 1
            else:
                result["updated_count"] += 1
        except Exception as exc:
            db.rollback()
            result["failed_count"] += 1
            result["failures"].append(
                {
                    "source_person_id": source_id,
                    "target_person_id": target_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return result


def build_identity_report_payload(
    db: Session,
    repo: GraphRepository,
    record: dict[str, Any],
) -> dict[str, Any]:
    source_id = int(record["source_person_id"])
    target_id = int(record["target_person_id"])
    source_name = record.get("source_name")
    target_name = record.get("target_name")
    source_evidence = repo.get_person_evidence_bundle(person_id=source_id, max_hops=3)
    target_evidence = repo.get_person_evidence_bundle(person_id=target_id, max_hops=3)

    return {
        "target_person_name": source_name or target_name or "",
        "person_1": {
            "person_id": source_id,
            "name": source_name,
            "passage_texts": _load_person_passages(db, repo, source_id),
            "graph": source_evidence,
        },
        "person_2": {
            "person_id": target_id,
            "name": target_name,
            "passage_texts": _load_person_passages(db, repo, target_id),
            "graph": target_evidence,
        },
    }


def call_identity_report_llm(payload: dict[str, Any]) -> str:
    provider = get_llm_provider()
    messages = [
        {"role": "system", "content": IDENTITY_REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请根据以下输入信息输出分析报告：\n"
                + json.dumps(payload, ensure_ascii=False, default=str)
            ),
        },
    ]
    return traced_chat_completion(
        provider=provider,
        messages=messages,
        metadata={
            "skill_code": "identity_ai_report",
            "purpose": "identity_ai_report",
            "response_format": "text",
        },
    )


def _load_existing_reports(
    db: Session,
    records: list[dict[str, Any]],
) -> dict[tuple[int, int], IdentityAIReport]:
    pairs = [
        normalize_pair(int(record["source_person_id"]), int(record["target_person_id"]))
        for record in records
    ]
    if not pairs:
        return {}
    clauses = [
        and_(
            IdentityAIReport.source_person_id == source_id,
            IdentityAIReport.target_person_id == target_id,
        )
        for source_id, target_id in pairs
    ]
    rows = db.query(IdentityAIReport).filter(or_(*clauses)).all()
    return {(row.source_person_id, row.target_person_id): row for row in rows}


def _normalized_names(record: dict[str, Any]) -> tuple[str | None, str | None]:
    source_id = int(record["source_person_id"])
    target_id = int(record["target_person_id"])
    if source_id <= target_id:
        return record.get("source_name"), record.get("target_name")
    return record.get("target_name"), record.get("source_name")


def _load_person_passages(
    db: Session,
    repo: GraphRepository,
    person_id: int,
) -> list[dict[str, Any]]:
    try:
        doc_ids = repo.get_person_passage_doc_ids(person_id)
    except Exception:
        doc_ids = []
    if not doc_ids:
        return []
    rows = db.query(Passage).filter(Passage.doc_id.in_(doc_ids)).all()
    by_id = {row.doc_id: row for row in rows}
    return [
        {
            "doc_id": doc_id,
            "title": by_id[doc_id].title,
            "context": by_id[doc_id].context,
        }
        for doc_id in doc_ids
        if doc_id in by_id
    ]
