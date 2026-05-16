"""图数据库统一访问层。

两层接口：
- 通用 run_read_query / run_write_query：执行任意 Cypher，主要给查询、调试用。
- 业务封装 upsert_xxx：把功能 A 涉及的所有节点/关系操作集中维护，
  Skill 内部不再写裸 Cypher，便于审计与日后做 schema 演进。
"""

from __future__ import annotations

from typing import Any

from app.graph.client import Neo4jClient


class GraphRepository:
    """图数据库仓储层。"""

    def __init__(self, client: Neo4jClient | None = None) -> None:
        self.client = client or Neo4jClient()

    # ---------- 通用底层 ----------

    def _extract_query(
        self, cypher: str, parameters: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        """从 parameters 中提取真正要执行的 Cypher 与绑定参数。"""

        query_parameters = dict(parameters or {})
        actual_cypher = (
            query_parameters.pop("cypher", None)
            or query_parameters.pop("query", None)
            or cypher
        )
        if not actual_cypher or not str(actual_cypher).strip():
            raise ValueError("未提供可执行的 Neo4j Cypher 语句。")
        return str(actual_cypher).strip(), query_parameters

    def _build_summary(self, summary) -> dict[str, Any]:
        counters = summary.counters
        return {
            "query_type": summary.query_type,
            "database": summary.database,
            "contains_updates": counters.contains_updates,
            "nodes_created": counters.nodes_created,
            "nodes_deleted": counters.nodes_deleted,
            "relationships_created": counters.relationships_created,
            "relationships_deleted": counters.relationships_deleted,
            "properties_set": counters.properties_set,
            "labels_added": counters.labels_added,
            "labels_removed": counters.labels_removed,
        }

    def run_read_query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        actual_cypher, query_parameters = self._extract_query(cypher, parameters)
        driver = self.client.get_driver()
        with driver.session(database=self.client.settings.neo4j_database) as session:
            result = session.run(actual_cypher, query_parameters)
            records = [record.data() for record in result]
            summary = result.consume()
        return {
            "status": "success",
            "operation": "read",
            "cypher": actual_cypher,
            "parameters": query_parameters,
            "record_count": len(records),
            "records": records,
            "summary": self._build_summary(summary),
        }

    def run_write_query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        actual_cypher, query_parameters = self._extract_query(cypher, parameters)
        driver = self.client.get_driver()
        with driver.session(database=self.client.settings.neo4j_database) as session:
            result = session.run(actual_cypher, query_parameters)
            records = [record.data() for record in result]
            summary = result.consume()
        return {
            "status": "success",
            "operation": "write",
            "cypher": actual_cypher,
            "parameters": query_parameters,
            "record_count": len(records),
            "records": records,
            "summary": self._build_summary(summary),
        }

    # ---------- 业务封装：功能 A ----------

    def upsert_passage_info(
        self,
        doc_id: int,
        title: str,
        source_type: int,
        era: str | None,
    ) -> dict[str, Any]:
        """Stage 1：写文章节点。注意 context 不入图。"""

        return self.run_write_query(
            cypher="""
            MERGE (p:Passage_Info {doc_id: $doc_id})
            SET p.title = $title,
                p.source_type = $source_type,
                p.era = $era
            RETURN p
            """,
            parameters={
                "doc_id": doc_id,
                "title": title,
                "source_type": source_type,
                "era": era,
            },
        )

    def upsert_person(
        self,
        person_id: int,
        name: str,
        zi: str | None,
        titles: list[str],
        level: int,
        passage_doc_id: int,
    ) -> dict[str, Any]:
        """Stage 2：写人物节点 + 与文章的 :在文章中 边（level 作为边属性）。"""

        return self.run_write_query(
            cypher="""
            MERGE (n:Person_Nodes {person_id: $person_id})
            SET n.name = $name,
                n.zi = $zi,
                n.titles = $titles
            WITH n
            MATCH (p:Passage_Info {doc_id: $passage_doc_id})
            MERGE (n)-[r:在文章中]->(p)
            SET r.level = $level
            RETURN n
            """,
            parameters={
                "person_id": person_id,
                "name": name,
                "zi": zi,
                "titles": titles,
                "level": level,
                "passage_doc_id": passage_doc_id,
            },
        )

    def upsert_life_event(
        self,
        person_id: int,
        event_id: str,
        event_type: str,
        time: dict[str, Any] | None,
        location: dict[str, Any] | None,
        official_title: str | None,
    ) -> dict[str, Any]:
        """Stage 3 (Skill-4)：写生平事件 + 时间/地点/官职子节点 + 边。

        - 籍贯事件不建 Time
        - 任职事件可附 official_title
        - location 全空跳过
        """

        params: dict[str, Any] = {
            "person_id": person_id,
            "event_id": event_id,
            "event_type": event_type,
            "official_title": official_title,
        }
        cypher_parts: list[str] = [
            "MATCH (n:Person_Nodes {person_id: $person_id})",
            "MERGE (le:Life_Events {event_id: $event_id})",
            "SET le.event_type = $event_type",
            "MERGE (n)-[:生平]->(le)",
        ]

        if event_type != "籍贯" and time and time.get("era"):
            params["era"] = time["era"]
            params["year"] = time.get("year")
            params["month"] = time.get("month")
            params["day"] = time.get("day")
            cypher_parts += [
                "MERGE (t:Time {era: $era, year: coalesce($year, -1)})",
                "SET t.month = $month, t.day = $day",
                "MERGE (le)-[:发生于]->(t)",
            ]

        if location and any(
            location.get(k) for k in ("dao", "fu", "zhou", "jun", "xian", "other")
        ):
            params["loc"] = {
                k: location.get(k) for k in ("dao", "fu", "zhou", "jun", "xian", "other")
            }
            cypher_parts += [
                "MERGE (l:Location {"
                "dao: coalesce($loc.dao, ''), "
                "fu: coalesce($loc.fu, ''), "
                "zhou: coalesce($loc.zhou, ''), "
                "jun: coalesce($loc.jun, ''), "
                "xian: coalesce($loc.xian, ''), "
                "other: coalesce($loc.other, '')})",
                "MERGE (le)-[:发生于]->(l)",
            ]

        if event_type == "任职" and official_title:
            cypher_parts += [
                "MERGE (o:Official_title {official_title: $official_title})",
                "MERGE (le)-[:担任]->(o)",
            ]

        cypher_parts.append("RETURN le")
        return self.run_write_query(cypher="\n".join(cypher_parts), parameters=params)

    def upsert_historical_event_time_link(
        self,
        event_name: str,
        time: dict[str, Any],
    ) -> dict[str, Any]:
        """Stage 3 (Skill-4 第 3 条)：把已知历史事件挂到 Time 节点。"""

        return self.run_write_query(
            cypher="""
            MERGE (h:Historical_Events {event_name: $event_name})
            MERGE (t:Time {era: $era, year: coalesce($year, -1)})
            SET t.month = coalesce($month, t.month),
                t.day = coalesce($day, t.day)
            MERGE (h)-[:发生于]->(t)
            RETURN h
            """,
            parameters={
                "event_name": event_name,
                "era": time.get("era"),
                "year": time.get("year"),
                "month": time.get("month"),
                "day": time.get("day"),
            },
        )

    def upsert_person_historical_event(
        self,
        person_id: int,
        event_name: str,
        relation_label: str,
    ) -> dict[str, Any]:
        """Stage 3 (Skill-5)：人物 ↔ 历史事件 边，label≤15 字。"""

        return self.run_write_query(
            cypher="""
            MERGE (h:Historical_Events {event_name: $event_name})
            WITH h
            MATCH (n:Person_Nodes {person_id: $person_id})
            MERGE (n)-[r:历史事件]->(h)
            SET r.label = $relation_label
            RETURN n, h
            """,
            parameters={
                "person_id": person_id,
                "event_name": event_name,
                "relation_label": relation_label,
            },
        )

    def upsert_person_relation(
        self,
        source_person_id: int,
        target_person_id: int,
        codes: list[str],
        note: str | None = None,
    ) -> dict[str, Any]:
        """Stage 3 (Skill-7)：人物 ↔ 人物 字母关系边（单向，调用方负责双向）。"""

        return self.run_write_query(
            cypher="""
            MATCH (a:Person_Nodes {person_id: $source_person_id})
            MATCH (b:Person_Nodes {person_id: $target_person_id})
            MERGE (a)-[r:person_relation]->(b)
            SET r.codes = $codes, r.note = $note
            RETURN r
            """,
            parameters={
                "source_person_id": source_person_id,
                "target_person_id": target_person_id,
                "codes": codes,
                "note": note,
            },
        )

    # ---------- 业务封装：功能 B ----------

    def merge_same_name_persons_for_passage(self, doc_id: int) -> dict[str, Any]:
        """功能 B：把指定篇目中的人物按姓名精确合并到既有人物节点。

        这个版本不做 LLM 裁定，也不接 CBDB；只有当新篇目人物与更早篇目人物
        的 name 完全相等时，才把新人物节点合并到最早的同名人物节点上。
        """

        pair_result = self.run_read_query(
            cypher="""
            MATCH (new_person:Person_Nodes)-[:在文章中]->(:Passage_Info {doc_id: $doc_id})
            WHERE new_person.name IS NOT NULL AND trim(new_person.name) <> ''
            MATCH (candidate:Person_Nodes {name: new_person.name})-[:在文章中]->(old_passage:Passage_Info)
            WHERE candidate.person_id <> new_person.person_id
              AND old_passage.doc_id < $doc_id
            WITH new_person, collect(DISTINCT candidate.person_id) AS candidate_ids
            WITH new_person,
                 candidate_ids,
                 reduce(
                    min_id = candidate_ids[0],
                    id IN candidate_ids |
                    CASE WHEN id < min_id THEN id ELSE min_id END
                 ) AS canonical_id
            RETURN new_person.person_id AS duplicate_person_id,
                   new_person.name AS name,
                   canonical_id AS canonical_person_id,
                   size(candidate_ids) AS candidate_count
            ORDER BY name, duplicate_person_id
            """,
            parameters={"doc_id": doc_id},
        )

        merges: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for record in pair_result.get("records", []):
            duplicate_person_id = int(record["duplicate_person_id"])
            canonical_person_id = int(record["canonical_person_id"])
            if duplicate_person_id == canonical_person_id:
                continue

            try:
                result = self.merge_person_nodes(
                    canonical_person_id=canonical_person_id,
                    duplicate_person_id=duplicate_person_id,
                )
            except Exception as exc:  # pragma: no cover - depends on Neo4j runtime
                failures.append(
                    {
                        "name": record.get("name"),
                        "canonical_person_id": canonical_person_id,
                        "duplicate_person_id": duplicate_person_id,
                        "error": str(exc),
                    }
                )
                continue

            merges.append(
                {
                    "name": record.get("name"),
                    "canonical_person_id": canonical_person_id,
                    "duplicate_person_id": duplicate_person_id,
                    "candidate_count": int(record.get("candidate_count") or 0),
                    "result": result,
                }
            )

        return {
            "status": "partial" if failures else "success",
            "doc_id": doc_id,
            "matched_person_count": len(pair_result.get("records", [])),
            "merged_person_count": len(merges),
            "failed_merge_count": len(failures),
            "merges": merges,
            "failures": failures,
        }

    def merge_person_nodes(
        self,
        canonical_person_id: int,
        duplicate_person_id: int,
    ) -> dict[str, Any]:
        """把 duplicate 人物节点的已知关系转移到 canonical 人物节点后删除 duplicate。"""

        parameters = {
            "canonical_person_id": canonical_person_id,
            "duplicate_person_id": duplicate_person_id,
        }
        steps = [
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})
            SET keep.zi = coalesce(keep.zi, drop.zi),
                keep.titles = reduce(
                    acc = [],
                    title IN coalesce(keep.titles, []) + coalesce(drop.titles, []) |
                    CASE WHEN title IS NULL OR title IN acc THEN acc ELSE acc + title END
                ),
                keep.merged_person_ids = reduce(
                    acc = [],
                    pid IN coalesce(keep.merged_person_ids, []) +
                           coalesce(drop.merged_person_ids, []) +
                           [drop.person_id] |
                    CASE WHEN pid IS NULL OR pid IN acc THEN acc ELSE acc + pid END
                )
            RETURN keep
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})-[r:在文章中]->(passage:Passage_Info)
            MERGE (keep)-[new_rel:在文章中]->(passage)
            SET new_rel.level =
                CASE
                    WHEN new_rel.level IS NULL THEN r.level
                    WHEN r.level IS NULL THEN new_rel.level
                    WHEN r.level < new_rel.level THEN r.level
                    ELSE new_rel.level
                END
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})-[:生平]->(event:Life_Events)
            MERGE (keep)-[:生平]->(event)
            RETURN count(event) AS transferred
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})-[r:历史事件]->(event:Historical_Events)
            MERGE (keep)-[new_rel:历史事件]->(event)
            SET new_rel.label = coalesce(new_rel.label, r.label)
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})-[r:person_relation]->(target:Person_Nodes)
            WHERE target.person_id <> $canonical_person_id
              AND target.person_id <> $duplicate_person_id
            MERGE (keep)-[new_rel:person_relation]->(target)
            SET new_rel.codes = coalesce(new_rel.codes, r.codes),
                new_rel.note = coalesce(new_rel.note, r.note)
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (source:Person_Nodes)-[r:person_relation]->(drop:Person_Nodes {person_id: $duplicate_person_id})
            WHERE source.person_id <> $canonical_person_id
              AND source.person_id <> $duplicate_person_id
            MERGE (source)-[new_rel:person_relation]->(keep)
            SET new_rel.codes = coalesce(new_rel.codes, r.codes),
                new_rel.note = coalesce(new_rel.note, r.note)
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})
            DETACH DELETE drop
            RETURN $duplicate_person_id AS deleted_person_id
            """,
        ]

        step_results = [
            self.run_write_query(cypher=cypher, parameters=parameters)
            for cypher in steps
        ]
        return {
            "status": "success",
            "canonical_person_id": canonical_person_id,
            "duplicate_person_id": duplicate_person_id,
            "step_count": len(step_results),
            "steps": step_results,
        }
