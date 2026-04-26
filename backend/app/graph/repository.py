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
