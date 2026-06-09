"""图数据库统一访问层。

两层接口：
- 通用 run_read_query / run_write_query：执行任意 Cypher，主要给查询、调试用。
- 业务封装 upsert_xxx：把功能 A 涉及的所有节点/关系操作集中维护，
  Skill 内部不再写裸 Cypher，便于审计与日后做 schema 演进。
"""

from __future__ import annotations

import json
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
        evidence: str | None = None,
        source_doc_id: int | None = None,
        direction_verified: bool = False,
    ) -> dict[str, Any]:
        """Stage 3 (Skill-7)：写入带来源和方向校验状态的人物关系边。"""

        return self.run_write_query(
            cypher="""
            MATCH (a:Person_Nodes {person_id: $source_person_id})
            MATCH (b:Person_Nodes {person_id: $target_person_id})
            MERGE (a)-[r:person_relation]->(b)
            SET r.codes = $codes,
                r.note = $note,
                r.evidence = $evidence,
                r.source_doc_id = $source_doc_id,
                r.direction_verified = $direction_verified
            RETURN r
            """,
            parameters={
                "source_person_id": source_person_id,
                "target_person_id": target_person_id,
                "codes": codes,
                "note": note,
                "evidence": evidence,
                "source_doc_id": source_doc_id,
                "direction_verified": direction_verified,
            },
        )

    # ---------- 业务封装：功能 B ----------

    def find_same_name_person_candidates_for_passage(self, doc_id: int) -> dict[str, Any]:
        """Recall same-name candidates for Function B without merging them."""

        return self.run_read_query(
            cypher="""
            MATCH (new_person:Person_Nodes)-[new_rel:在文章中]->(new_passage:Passage_Info {doc_id: $doc_id})
            WHERE new_person.name IS NOT NULL AND trim(new_person.name) <> ''
            MATCH (candidate:Person_Nodes {name: new_person.name})-[candidate_rel:在文章中]->(candidate_passage:Passage_Info)
            WHERE candidate.person_id <> new_person.person_id
              AND candidate_passage.doc_id < $doc_id
            WITH new_person,
                 new_rel,
                 new_passage,
                 candidate,
                 collect(DISTINCT {
                    doc_id: candidate_passage.doc_id,
                    title: candidate_passage.title,
                    source_type: candidate_passage.source_type,
                    level: candidate_rel.level
                 }) AS candidate_passages,
                 min(candidate_passage.doc_id) AS first_candidate_doc_id,
                 max(candidate_passage.doc_id) AS latest_candidate_doc_id
            ORDER BY latest_candidate_doc_id DESC, candidate.person_id ASC
            WITH new_person,
                 new_rel,
                 new_passage,
                 collect({
                    candidate_person_id: candidate.person_id,
                    candidate_passage_doc_id: first_candidate_doc_id,
                    latest_candidate_passage_doc_id: latest_candidate_doc_id,
                    candidate_passages: candidate_passages
                 }) AS candidate_rows
            UNWIND candidate_rows AS row
            RETURN new_person.person_id AS new_person_id,
                   new_person.name AS name,
                   new_passage.doc_id AS new_passage_doc_id,
                   new_passage.title AS new_passage_title,
                   new_passage.source_type AS new_passage_source_type,
                   new_rel.level AS new_level,
                   row.candidate_person_id AS candidate_person_id,
                   row.candidate_passage_doc_id AS candidate_passage_doc_id,
                   row.latest_candidate_passage_doc_id AS latest_candidate_passage_doc_id,
                   row.candidate_passages AS candidate_passages,
                   size(candidate_rows) AS candidate_count_for_new_person
            ORDER BY name, new_person_id, latest_candidate_passage_doc_id DESC, candidate_person_id
            """,
            parameters={"doc_id": doc_id},
        )

    def get_person_evidence_bundle(
        self,
        person_id: int,
        max_hops: int = 2,
        focus: list[str] | None = None,
        incremental: bool = False,
    ) -> dict[str, Any]:
        """构造同人裁定白名单证据。

        首轮返回人物自身的两跳基础证据。后续轮次设置 ``incremental=True``，
        只返回当前跳数新出现的关系路径和定向周边证据，调用侧负责累计去重。
        """

        hops = max(1, min(int(max_hops), 5))
        focus_set = {str(item) for item in (focus or []) if item}
        is_increment = incremental and hops > 2

        basic = self.run_read_query(
            cypher="""
            MATCH (p:Person_Nodes {person_id: $person_id})
            RETURN p {
                .person_id,
                .name,
                .zi,
                .titles,
                .merged_person_ids
            } AS person
            """,
            parameters={"person_id": person_id},
        )
        records = basic.get("records", [])
        base_record = records[0] if records else {}
        bundle: dict[str, Any] = {
            "person": base_record.get("person") or {"person_id": person_id},
            "passages": [],
            "life_events": [],
            "relations": [],
            "historical_events": [],
            "relation_paths": [],
            "related_person_evidence": [],
        }

        if not is_increment:
            passages = self.run_read_query(
                cypher="""
                MATCH (p:Person_Nodes {person_id: $person_id})-[r:在文章中]->(pa:Passage_Info)
                RETURN pa {
                    .doc_id,
                    .title,
                    .source_type,
                    .era
                } AS passage,
                r.level AS level,
                'Person_Nodes-在文章中-Passage_Info' AS path
                ORDER BY pa.doc_id
                """,
                parameters={"person_id": person_id},
            )
            bundle["passages"] = [
                {
                    **(record.get("passage") or {}),
                    "level": record.get("level"),
                    "path": record.get("path"),
                }
                for record in passages.get("records", [])
                if (record.get("passage") or {}).get("doc_id") is not None
            ]

        wants_events = not focus_set or bool(focus_set & {"events", "official_titles", "locations"})
        wants_relations = not focus_set or "relations" in focus_set

        if not is_increment and wants_events:
            events = self.run_read_query(
                cypher="""
                MATCH (p:Person_Nodes {person_id: $person_id})-[:生平]->(le:Life_Events)
                OPTIONAL MATCH (le)-[:发生于]->(t:Time)
                OPTIONAL MATCH (le)-[:发生于]->(l:Location)
                OPTIONAL MATCH (le)-[:担任]->(o:Official_title)
                RETURN le {
                    .event_id,
                    .event_type
                } AS life_event,
                t {
                    .era,
                    .year,
                    .month,
                    .day
                } AS time,
                l {
                    .dao,
                    .fu,
                    .zhou,
                    .jun,
                    .xian,
                    .other
                } AS location,
                o {.official_title} AS official_title,
                'Person_Nodes-生平-Life_Events-(发生于/担任)' AS path
                LIMIT 80
                """,
                parameters={"person_id": person_id},
            )
            bundle["life_events"] = events.get("records", [])

            historical_events = self.run_read_query(
                cypher="""
                MATCH (p:Person_Nodes {person_id: $person_id})-[r:历史事件]->(h:Historical_Events)
                OPTIONAL MATCH (h)-[:发生于]->(ht:Time)
                RETURN h {.event_name} AS historical_event,
                       r.label AS label,
                       collect(DISTINCT ht {.era, .year, .month, .day}) AS times,
                       'Person_Nodes-历史事件-Historical_Events-发生于-Time' AS path
                LIMIT 50
                """,
                parameters={"person_id": person_id},
            )
            bundle["historical_events"] = historical_events.get("records", [])

        if not is_increment and wants_relations:
            relations = self.run_read_query(
                cypher="""
                MATCH (p:Person_Nodes {person_id: $person_id})-[r:person_relation]->(other:Person_Nodes)
                RETURN 'outgoing' AS direction,
                       other {.person_id, .name, .zi, .titles, .merged_person_ids} AS other_person,
                       r.codes AS codes,
                       r.note AS note,
                       r.evidence AS evidence,
                       r.source_doc_id AS source_doc_id,
                       r.direction_verified AS direction_verified,
                       'Person_Nodes-person_relation-Person_Nodes' AS path
                UNION
                MATCH (other:Person_Nodes)-[r:person_relation]->(p:Person_Nodes {person_id: $person_id})
                RETURN 'incoming' AS direction,
                       other {.person_id, .name, .zi, .titles, .merged_person_ids} AS other_person,
                       r.codes AS codes,
                       r.note AS note,
                       r.evidence AS evidence,
                       r.source_doc_id AS source_doc_id,
                       r.direction_verified AS direction_verified,
                       'Person_Nodes-person_relation-Person_Nodes' AS path
                LIMIT 80
                """,
                parameters={"person_id": person_id},
            )
            bundle["relations"] = relations.get("records", [])

        if is_increment and wants_relations:
            relation_paths = self.run_read_query(
                cypher=f"""
                MATCH path=(p:Person_Nodes {{person_id: $person_id}})
                           -[:person_relation*{hops}..{hops}]-
                           (other:Person_Nodes)
                WHERE other.person_id <> $person_id
                  AND all(node IN nodes(path) WHERE single(x IN nodes(path) WHERE x = node))
                WITH path,
                     other,
                     size([rel IN relationships(path) WHERE rel.evidence IS NOT NULL]) AS evidence_count,
                     size([rel IN relationships(path) WHERE coalesce(rel.direction_verified, false)]) AS verified_count
                ORDER BY evidence_count DESC, verified_count DESC, other.person_id
                RETURN [node IN nodes(path) |
                            node {{.person_id, .name, .zi, .titles, .merged_person_ids}}
                       ] AS persons,
                       [rel IN relationships(path) | {{
                            codes: rel.codes,
                            note: rel.note,
                            evidence: rel.evidence,
                            source_doc_id: rel.source_doc_id,
                            direction_verified: rel.direction_verified
                       }}] AS relations,
                       evidence_count,
                       verified_count,
                       'Person_Nodes-person_relation*{hops}-Person_Nodes' AS path
                LIMIT $limit
                """,
                parameters={"person_id": person_id, "limit": 60},
            )
            bundle["relation_paths"] = relation_paths.get("records", [])

        if is_increment and wants_events:
            relation_depth = hops - 2
            focus_values = focus_set or {"events", "official_titles", "locations"}
            related_records: list[dict[str, Any]] = []

            if "official_titles" in focus_values:
                titles = self.run_read_query(
                    cypher=f"""
                    MATCH person_path=(p:Person_Nodes {{person_id: $person_id}})
                                      -[:person_relation*{relation_depth}..{relation_depth}]-
                                      (other:Person_Nodes)
                    MATCH (other)-[:生平]->(le:Life_Events)-[:担任]->(o:Official_title)
                    OPTIONAL MATCH (le)-[:发生于]->(t:Time)
                    RETURN 'official_title' AS category,
                           other {{.person_id, .name, .zi, .titles, .merged_person_ids}} AS other_person,
                           le {{.event_id, .event_type}} AS life_event,
                           o {{.official_title}} AS official_title,
                           t {{.era, .year, .month, .day}} AS time,
                           [rel IN relationships(person_path) | {{
                               codes: rel.codes,
                               note: rel.note,
                               evidence: rel.evidence,
                               source_doc_id: rel.source_doc_id,
                               direction_verified: rel.direction_verified
                           }}] AS relation_chain,
                           'Person_Nodes-person_relation*{relation_depth}-Person_Nodes-生平-Life_Events-担任-Official_title' AS path
                    ORDER BY other.person_id, le.event_id, o.official_title
                    LIMIT $limit
                    """,
                    parameters={"person_id": person_id, "limit": 60},
                )
                related_records.extend(titles.get("records", []))

            if "locations" in focus_values:
                locations = self.run_read_query(
                    cypher=f"""
                    MATCH person_path=(p:Person_Nodes {{person_id: $person_id}})
                                      -[:person_relation*{relation_depth}..{relation_depth}]-
                                      (other:Person_Nodes)
                    MATCH (other)-[:生平]->(le:Life_Events)-[:发生于]->(l:Location)
                    RETURN 'location' AS category,
                           other {{.person_id, .name, .zi, .titles, .merged_person_ids}} AS other_person,
                           le {{.event_id, .event_type}} AS life_event,
                           l {{.dao, .fu, .zhou, .jun, .xian, .other}} AS location,
                           [rel IN relationships(person_path) | {{
                               codes: rel.codes,
                               note: rel.note,
                               evidence: rel.evidence,
                               source_doc_id: rel.source_doc_id,
                               direction_verified: rel.direction_verified
                           }}] AS relation_chain,
                           'Person_Nodes-person_relation*{relation_depth}-Person_Nodes-生平-Life_Events-发生于-Location' AS path
                    ORDER BY other.person_id, le.event_id
                    LIMIT $limit
                    """,
                    parameters={"person_id": person_id, "limit": 60},
                )
                related_records.extend(locations.get("records", []))

            if "events" in focus_values:
                events = self.run_read_query(
                    cypher=f"""
                    MATCH person_path=(p:Person_Nodes {{person_id: $person_id}})
                                      -[:person_relation*{relation_depth}..{relation_depth}]-
                                      (other:Person_Nodes)
                    MATCH (other)-[:生平]->(le:Life_Events)
                    OPTIONAL MATCH (le)-[:发生于]->(t:Time)
                    OPTIONAL MATCH (le)-[:发生于]->(l:Location)
                    OPTIONAL MATCH (le)-[:担任]->(o:Official_title)
                    RETURN 'life_event' AS category,
                           other {{.person_id, .name, .zi, .titles, .merged_person_ids}} AS other_person,
                           le {{.event_id, .event_type}} AS life_event,
                           t {{.era, .year, .month, .day}} AS time,
                           l {{.dao, .fu, .zhou, .jun, .xian, .other}} AS location,
                           o {{.official_title}} AS official_title,
                           [rel IN relationships(person_path) | {{
                               codes: rel.codes,
                               note: rel.note,
                               evidence: rel.evidence,
                               source_doc_id: rel.source_doc_id,
                               direction_verified: rel.direction_verified
                           }}] AS relation_chain,
                           'Person_Nodes-person_relation*{relation_depth}-Person_Nodes-生平-Life_Events' AS path
                    ORDER BY other.person_id, le.event_id
                    LIMIT $limit
                    """,
                    parameters={"person_id": person_id, "limit": 60},
                )
                related_records.extend(events.get("records", []))

                historical = self.run_read_query(
                    cypher=f"""
                    MATCH person_path=(p:Person_Nodes {{person_id: $person_id}})
                                      -[:person_relation*{relation_depth}..{relation_depth}]-
                                      (other:Person_Nodes)
                    MATCH (other)-[r:历史事件]->(h:Historical_Events)
                    OPTIONAL MATCH (h)-[:发生于]->(t:Time)
                    WITH person_path,
                         other,
                         r,
                         h,
                         collect(DISTINCT t {{.era, .year, .month, .day}}) AS times
                    ORDER BY other.person_id, h.event_name
                    RETURN 'historical_event' AS category,
                           other {{.person_id, .name, .zi, .titles, .merged_person_ids}} AS other_person,
                           h {{.event_name}} AS historical_event,
                           r.label AS label,
                           times,
                           [rel IN relationships(person_path) | {{
                               codes: rel.codes,
                               note: rel.note,
                               evidence: rel.evidence,
                               source_doc_id: rel.source_doc_id,
                               direction_verified: rel.direction_verified
                           }}] AS relation_chain,
                           'Person_Nodes-person_relation*{relation_depth}-Person_Nodes-历史事件-Historical_Events-发生于-Time' AS path
                    LIMIT $limit
                    """,
                    parameters={"person_id": person_id, "limit": 60},
                )
                related_records.extend(historical.get("records", []))

            bundle["related_person_evidence"] = related_records

        bundle["max_hops"] = hops
        bundle["focus"] = sorted(focus_set)
        bundle["incremental"] = is_increment
        bundle["limits"] = {
            "relation_paths_per_hop": 60,
            "related_records_per_focus": 60,
        }
        return bundle

    def mark_possible_same_person(
        self,
        source_person_id: int,
        target_person_id: int,
        confidence: float,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a review edge when evidence is insufficient for a safe merge."""

        return self.run_write_query(
            cypher="""
            MATCH (source:Person_Nodes {person_id: $source_person_id})
            MATCH (target:Person_Nodes {person_id: $target_person_id})
            MERGE (source)-[r:可能同人]->(target)
            SET r.status = '待人工判断',
                r.confidence = $confidence,
                r.reason = $reason,
                r.evidence_json = $evidence_json
            RETURN source.person_id AS source_person_id,
                   target.person_id AS target_person_id,
                   r.status AS status,
                   r.confidence AS confidence
            """,
            parameters={
                "source_person_id": source_person_id,
                "target_person_id": target_person_id,
                "confidence": confidence,
                "reason": reason,
                "evidence_json": json.dumps(evidence or {}, ensure_ascii=False),
            },
        )

    def merge_same_name_persons_for_passage(self, doc_id: int) -> dict[str, Any]:
        """已弃用：把指定篇目中的人物按姓名精确合并到既有人物节点。

        当前功能 B 入口是 person_identity_resolution_atomic。该方法仅为旧
        person_exact_match_merge_atomic 兼容保留，不应被新 workflow 调用。
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

    # ---------- 辅助：图谱元素提取 ----------

    @staticmethod
    def extract_graph_elements(records: list[dict]) -> dict:
        """从 Neo4j 查询结果 records 中提取节点和边，供前端可视化。

        输入：任意 Neo4j records 列表（每条 record 是 dict）。
        输出：{"nodes": [...], "edges": [...]}，已按 id / source+target+label 去重。
        """

        nodes: dict[str, dict] = {}  # id -> node
        edges: dict[str, dict] = {}  # source+target+label -> edge

        # 暂存扁平关系记录的信息，供全局补边使用
        # key: target_person_node_id, value: {"label": str, "properties": dict}
        flat_relation_info: dict[str, dict] = {}

        def _infer_node(d: dict) -> dict | None:
            """尝试把一个 dict 识别为图节点，返回节点 dict 或 None。"""
            if not isinstance(d, dict):
                return None

            if "person_id" in d:
                nid = f"Person_Nodes_{d['person_id']}"
                return {
                    "id": nid,
                    "label": d.get("name") or str(d["person_id"]),
                    "type": "Person_Nodes",
                    "properties": d,
                }
            if "doc_id" in d:
                nid = f"Passage_Info_{d['doc_id']}"
                return {
                    "id": nid,
                    "label": d.get("title") or str(d["doc_id"]),
                    "type": "Passage_Info",
                    "properties": d,
                }
            if "event_id" in d and "event_type" in d:
                nid = f"Life_Events_{d['event_id']}"
                return {
                    "id": nid,
                    "label": d.get("event_type") or str(d["event_id"]),
                    "type": "Life_Events",
                    "properties": d,
                }
            if "event_name" in d:
                nid = f"Historical_Events_{d['event_name']}"
                return {
                    "id": nid,
                    "label": d["event_name"],
                    "type": "Historical_Events",
                    "properties": d,
                }
            if "official_title" in d and isinstance(d.get("official_title"), str):
                nid = f"Official_title_{d['official_title']}"
                return {
                    "id": nid,
                    "label": d["official_title"],
                    "type": "Official_title",
                    "properties": d,
                }
            if "era" in d and "year" in d and "doc_id" not in d:
                label = f"{d.get('era', '')}{d.get('year', '')}"
                nid = f"Time_{label}"
                return {
                    "id": nid,
                    "label": label,
                    "type": "Time",
                    "properties": d,
                }
            loc_fields = ("dao", "zhou", "xian", "fu", "jun", "other")
            if any(d.get(k) for k in loc_fields):
                label_val = (
                    d.get("xian")
                    or d.get("jun")
                    or d.get("zhou")
                    or d.get("fu")
                    or d.get("dao")
                    or d.get("other")
                    or "未知地点"
                )
                nid = f"Location_{'_'.join(str(d.get(k) or '') for k in loc_fields)}"
                return {
                    "id": nid,
                    "label": label_val,
                    "type": "Location",
                    "properties": d,
                }
            # 扁平关系记录：RETURN other.name AS target_name, r.codes AS codes
            if "target_name" in d and isinstance(d["target_name"], str):
                name = d["target_name"]
                nid = f"Person_Nodes_name_{name}"
                codes = d.get("codes") or []
                note = d.get("note")
                if codes:
                    flat_relation_info[nid] = {
                        "label": ",".join(str(c) for c in codes),
                        "properties": {"codes": codes, "note": note},
                    }
                return {
                    "id": nid,
                    "label": name,
                    "type": "Person_Nodes",
                    "properties": {"name": name, "codes": codes, "note": note},
                }
            if "source_name" in d and isinstance(d["source_name"], str):
                name = d["source_name"]
                nid = f"Person_Nodes_name_{name}"
                codes = d.get("codes") or []
                note = d.get("note")
                if codes:
                    flat_relation_info[nid] = {
                        "label": ",".join(str(c) for c in codes),
                        "properties": {"codes": codes, "note": note},
                    }
                return {
                    "id": nid,
                    "label": name,
                    "type": "Person_Nodes",
                    "properties": {"name": name, "codes": codes, "note": note},
                }
            return None

        def _collect_nodes_from_dict(d: dict) -> list[dict]:
            """递归搜索 dict，返回所有可识别节点。"""
            found: list[dict] = []
            node = _infer_node(d)
            if node:
                found.append(node)
            else:
                for v in d.values():
                    if isinstance(v, dict):
                        found.extend(_collect_nodes_from_dict(v))
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                found.extend(_collect_nodes_from_dict(item))
            return found

        def _add_node(node: dict) -> None:
            nid = node["id"]
            if nid not in nodes:
                nodes[nid] = node

        def _add_edge(source: str, target: str, label: str, props: dict | None = None) -> None:
            key = f"{source}__{target}__{label}"
            if key not in edges:
                edges[key] = {
                    "source": source,
                    "target": target,
                    "label": label,
                    "properties": props or {},
                }

        for record in records:
            if not isinstance(record, dict):
                continue

            # 收集 record 中所有可识别节点
            record_nodes: list[dict] = []
            # 先尝试 record 本身（扁平记录如 target_name+codes 的值不是 dict）
            top_node = _infer_node(record)
            if top_node:
                record_nodes.append(top_node)
            else:
                for v in record.values():
                    if isinstance(v, dict):
                        record_nodes.extend(_collect_nodes_from_dict(v))
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                record_nodes.extend(_collect_nodes_from_dict(item))

            for n in record_nodes:
                _add_node(n)

            # 根据同一 record 内的节点共现 + path 字段推断边
            path_str = record.get("path", "")

            # 按类型分组
            person_nodes = [n for n in record_nodes if n["type"] == "Person_Nodes"]
            passage_nodes = [n for n in record_nodes if n["type"] == "Passage_Info"]
            life_event_nodes = [n for n in record_nodes if n["type"] == "Life_Events"]
            time_nodes = [n for n in record_nodes if n["type"] == "Time"]
            loc_nodes = [n for n in record_nodes if n["type"] == "Location"]
            official_nodes = [n for n in record_nodes if n["type"] == "Official_title"]
            hist_event_nodes = [n for n in record_nodes if n["type"] == "Historical_Events"]

            # 1. Person → Passage（在文章中）
            for p in person_nodes:
                for pa in passage_nodes:
                    _add_edge(p["id"], pa["id"], "在文章中")

            # 2. Person → Life_Events（生平）
            for p in person_nodes:
                for le in life_event_nodes:
                    _add_edge(p["id"], le["id"], "生平")

            # 3. Life_Events → Time/Location（发生于）
            for le in life_event_nodes:
                for t in time_nodes:
                    _add_edge(le["id"], t["id"], "发生于")
                for lo in loc_nodes:
                    _add_edge(le["id"], lo["id"], "发生于")

            # 4. Life_Events → Official_title（担任）
            for le in life_event_nodes:
                for o in official_nodes:
                    _add_edge(le["id"], o["id"], "担任")

            # 5. Person → Historical_Events（历史事件）
            for p in person_nodes:
                for he in hist_event_nodes:
                    rel_label = record.get("label") or "历史事件"
                    _add_edge(p["id"], he["id"], rel_label)

            # 6. Person → Person（person_relation）
            if len(person_nodes) >= 2:
                direction = record.get("direction", "outgoing")
                codes = record.get("codes") or []
                rel_label = codes[0] if codes else "person_relation"
                if isinstance(path_str, str) and "person_relation" in path_str:
                    if direction == "incoming":
                        _add_edge(person_nodes[1]["id"], person_nodes[0]["id"], rel_label)
                    else:
                        _add_edge(person_nodes[0]["id"], person_nodes[1]["id"], rel_label)
                else:
                    # 无 path 时，如果有 direction/codes 也尝试连边
                    if record.get("direction") or record.get("codes"):
                        if direction == "incoming":
                            _add_edge(person_nodes[1]["id"], person_nodes[0]["id"], rel_label)
                        else:
                            _add_edge(person_nodes[0]["id"], person_nodes[1]["id"], rel_label)

            # 7. 如果没有 Life_Events 但有 Person + Time/Location/Official_title，
            #    说明是扁平化的结果，直接连 Person
            if not life_event_nodes:
                for p in person_nodes:
                    for t in time_nodes:
                        _add_edge(p["id"], t["id"], "时间")
                    for lo in loc_nodes:
                        _add_edge(p["id"], lo["id"], "地点")
                    for o in official_nodes:
                        _add_edge(p["id"], o["id"], "官职")

        # ── 全局补边：修复跨 record 的孤立子图 ──
        # 多个 tool_call 分别查询同一人物的不同方面时，不同 record
        # 中的节点无法通过共现推断建立连边，导致孤立子图。
        # 策略：找出没有任何边的「孤儿节点」，按图谱 schema 补边。

        def _connected_targets(label: str) -> set[str]:
            return {e["target"] for e in edges.values() if e["label"] == label}

        def _connected_sources(label: str) -> set[str]:
            return {e["source"] for e in edges.values() if e["label"] == label}

        def _all_connected() -> set[str]:
            s: set[str] = set()
            for e in edges.values():
                s.add(e["source"])
                s.add(e["target"])
            return s

        by_type: dict[str, list[str]] = {}
        for n in nodes.values():
            by_type.setdefault(n["type"], []).append(n["id"])

        person_ids = by_type.get("Person_Nodes", [])
        le_ids = by_type.get("Life_Events", [])
        pa_ids = by_type.get("Passage_Info", [])
        time_ids = by_type.get("Time", [])
        loc_ids = by_type.get("Location", [])
        ot_ids = by_type.get("Official_title", [])
        he_ids = by_type.get("Historical_Events", [])

        # 识别"主查询人物"：有真实 person_id 的节点优先于 name-based 节点
        real_person_ids = [p for p in person_ids if not p.startswith("Person_Nodes_name_")]
        name_person_ids = [p for p in person_ids if p.startswith("Person_Nodes_name_")]

        def _find_main_person() -> str | None:
            if not person_ids:
                return None
            if len(real_person_ids) == 1:
                return real_person_ids[0]
            if real_person_ids:
                ec = {
                    pid: sum(1 for e in edges.values() if e["source"] == pid or e["target"] == pid)
                    for pid in real_person_ids
                }
                return max(real_person_ids, key=lambda p: ec.get(p, 0))
            return person_ids[0]

        main_pid = _find_main_person()

        # 1. 孤儿 Life_Events → 连到主 Person
        connected_le = _connected_targets("生平")
        orphan_le = [nid for nid in le_ids if nid not in connected_le]
        if orphan_le and main_pid:
            for le_id in orphan_le:
                _add_edge(main_pid, le_id, "生平")

        # 2. 孤儿 Passage_Info → 连到主 Person
        connected_pa = _connected_targets("在文章中")
        orphan_pa = [nid for nid in pa_ids if nid not in connected_pa]
        if orphan_pa and main_pid:
            for pa_id in orphan_pa:
                _add_edge(main_pid, pa_id, "在文章中")

        # 3. 孤儿 Time / Location → 连到 Life_Events（优先）或主 Person
        connected_time = _connected_targets("发生于") | _connected_targets("时间")
        orphan_time = [nid for nid in time_ids if nid not in connected_time]
        if orphan_time:
            if le_ids:
                for t_id in orphan_time:
                    _add_edge(le_ids[0], t_id, "发生于")
            elif main_pid:
                for t_id in orphan_time:
                    _add_edge(main_pid, t_id, "时间")

        connected_loc = _connected_targets("发生于") | _connected_targets("地点")
        orphan_loc = [nid for nid in loc_ids if nid not in connected_loc]
        if orphan_loc:
            if le_ids:
                for l_id in orphan_loc:
                    _add_edge(le_ids[0], l_id, "发生于")
            elif main_pid:
                for l_id in orphan_loc:
                    _add_edge(main_pid, l_id, "地点")

        # 4. 孤儿 Official_title → 连到 Life_Events 或主 Person
        connected_ot = _connected_targets("担任") | _connected_targets("官职")
        orphan_ot = [nid for nid in ot_ids if nid not in connected_ot]
        if orphan_ot:
            if le_ids:
                for o_id in orphan_ot:
                    _add_edge(le_ids[0], o_id, "担任")
            elif main_pid:
                for o_id in orphan_ot:
                    _add_edge(main_pid, o_id, "官职")

        # 5. 孤儿 Historical_Events → 连到主 Person
        connected_he = _all_connected()
        orphan_he = [nid for nid in he_ids if nid not in connected_he]
        if orphan_he and main_pid:
            for he_id in orphan_he:
                _add_edge(main_pid, he_id, "历史事件")

        # 6. name-based 关系人物 → 连到主 Person，使用 flat_relation_info 的标签和属性
        if main_pid and name_person_ids:
            for np_id in name_person_ids:
                info = flat_relation_info.get(np_id, {})
                rel_label = info.get("label", "person_relation")
                rel_props = info.get("properties")
                _add_edge(main_pid, np_id, rel_label, rel_props)

        return {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
        }

    @staticmethod
    def build_graph_elements_from_evidence_bundles(
        bundles: list[dict],
    ) -> dict:
        """从 evidence bundle 列表直接构建图谱元素（节点+边）。

        与 extract_graph_elements 不同，此方法利用 evidence bundle 的已知结构
        直接建立正确的边，不依赖 record 内节点共现推断。
        """

        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}

        def _add_node(nid: str, label: str, ntype: str, props: dict) -> None:
            if nid not in nodes:
                nodes[nid] = {"id": nid, "label": label, "type": ntype, "properties": props}

        def _add_edge(source: str, target: str, label: str, props: dict | None = None) -> None:
            key = f"{source}__{target}__{label}"
            if key not in edges:
                edges[key] = {"source": source, "target": target, "label": label, "properties": props or {}}

        for bundle in bundles:
            person = bundle.get("person") or {}
            pid = person.get("person_id")
            if pid is None:
                continue
            person_nid = f"Person_Nodes_{pid}"
            _add_node(person_nid, person.get("name") or str(pid), "Person_Nodes", person)

            for p in bundle.get("passages") or []:
                if p.get("doc_id") is None:
                    continue
                pa_nid = f"Passage_Info_{p['doc_id']}"
                _add_node(pa_nid, p.get("title") or str(p["doc_id"]), "Passage_Info", p)
                _add_edge(person_nid, pa_nid, "在文章中")

            for rec in bundle.get("life_events") or []:
                le = rec.get("life_event") or rec
                eid = le.get("event_id")
                if eid is not None:
                    le_nid = f"Life_Events_{eid}"
                    _add_node(le_nid, le.get("event_type") or str(eid), "Life_Events", le)
                    _add_edge(person_nid, le_nid, "生平")

                    t = rec.get("time")
                    if isinstance(t, dict) and t.get("era") and t.get("year"):
                        t_label = f"{t['era']}{t['year']}"
                        t_nid = f"Time_{t_label}"
                        _add_node(t_nid, t_label, "Time", t)
                        _add_edge(le_nid, t_nid, "发生于")

                    loc = rec.get("location")
                    if isinstance(loc, dict):
                        loc_fields = ("dao", "zhou", "xian", "fu", "jun", "other")
                        if any(loc.get(k) for k in loc_fields):
                            loc_label = loc.get("xian") or loc.get("jun") or loc.get("zhou") or loc.get("fu") or loc.get("dao") or loc.get("other") or "未知"
                            loc_nid = f"Location_{'_'.join(str(loc.get(k) or '') for k in loc_fields)}"
                            _add_node(loc_nid, loc_label, "Location", loc)
                            _add_edge(le_nid, loc_nid, "发生于")

                    ot = rec.get("official_title")
                    if isinstance(ot, dict) and ot.get("official_title"):
                        ot_nid = f"Official_title_{ot['official_title']}"
                        _add_node(ot_nid, ot["official_title"], "Official_title", ot)
                        _add_edge(le_nid, ot_nid, "担任")

            for rec in bundle.get("relations") or []:
                other = rec.get("other_person")
                if not isinstance(other, dict) or other.get("person_id") is None:
                    continue
                other_nid = f"Person_Nodes_{other['person_id']}"
                _add_node(other_nid, other.get("name") or str(other["person_id"]), "Person_Nodes", other)
                codes = rec.get("codes") or []
                rel_label = ",".join(str(c) for c in codes) if codes else "person_relation"
                edge_props = {
                    "codes": codes,
                    "note": rec.get("note"),
                    "evidence": rec.get("evidence"),
                    "direction_verified": rec.get("direction_verified"),
                }
                direction = rec.get("direction", "outgoing")
                if direction == "incoming":
                    _add_edge(other_nid, person_nid, rel_label, edge_props)
                else:
                    _add_edge(person_nid, other_nid, rel_label, edge_props)

            for rec in bundle.get("historical_events") or []:
                he = rec.get("historical_event")
                if isinstance(he, dict) and he.get("event_name"):
                    he_nid = f"Historical_Events_{he['event_name']}"
                    _add_node(he_nid, he["event_name"], "Historical_Events", he)
                    _add_edge(person_nid, he_nid, rec.get("label") or "历史事件")

        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    @staticmethod
    def _graph_node_id(labels: list[str], properties: dict[str, Any], element_id: str) -> str:
        primary_label = labels[0] if labels else "Node"
        if "Person_Nodes" in labels and properties.get("person_id") is not None:
            return f"Person_Nodes_{properties['person_id']}"
        if "Passage_Info" in labels and properties.get("doc_id") is not None:
            return f"Passage_Info_{properties['doc_id']}"
        if "Life_Events" in labels and properties.get("event_id") is not None:
            return f"Life_Events_{properties['event_id']}"
        if "Historical_Events" in labels and properties.get("event_name"):
            return f"Historical_Events_{properties['event_name']}"
        if "Official_title" in labels and properties.get("official_title"):
            return f"Official_title_{properties['official_title']}"
        if "Time" in labels:
            parts = [
                str(properties.get("era") or ""),
                str(properties.get("year") or ""),
                str(properties.get("month") or ""),
                str(properties.get("day") or ""),
            ]
            return f"Time_{'_'.join(parts)}"
        if "Location" in labels:
            loc_fields = ("dao", "fu", "zhou", "jun", "xian", "other")
            return f"Location_{'_'.join(str(properties.get(k) or '') for k in loc_fields)}"
        return f"{primary_label}_{element_id}"

    @staticmethod
    def _graph_node_label(labels: list[str], properties: dict[str, Any], element_id: str) -> str:
        if "Person_Nodes" in labels:
            return str(properties.get("name") or properties.get("person_id") or element_id)
        if "Passage_Info" in labels:
            return str(properties.get("title") or properties.get("doc_id") or element_id)
        if "Life_Events" in labels:
            return str(properties.get("event_type") or properties.get("event_id") or element_id)
        if "Historical_Events" in labels:
            return str(properties.get("event_name") or element_id)
        if "Official_title" in labels:
            return str(properties.get("official_title") or element_id)
        if "Time" in labels:
            label = f"{properties.get('era') or ''}{properties.get('year') or ''}"
            return label or str(element_id)
        if "Location" in labels:
            for key in ("xian", "jun", "zhou", "fu", "dao", "other"):
                if properties.get(key):
                    return str(properties[key])
        return str(properties.get("name") or properties.get("title") or element_id)

    def get_person_neighborhood_graph_elements(
        self,
        person_ids: list[int],
        max_hops: int = 2,
    ) -> dict[str, Any]:
        """Return all graph nodes/relationships within N hops of the given persons."""

        normalized_person_ids = sorted({int(pid) for pid in person_ids})
        if not normalized_person_ids:
            return {"nodes": [], "edges": []}

        hops = max(1, min(int(max_hops), 2))
        result = self.run_read_query(
            cypher=f"""
            MATCH (root:Person_Nodes)
            WHERE root.person_id IN $person_ids
            OPTIONAL MATCH path=(root)-[*1..{hops}]-(neighbor)
            WHERE path IS NULL
               OR (
                 all(node IN nodes(path) WHERE single(x IN nodes(path) WHERE x = node))
                 AND all(
                   idx IN range(1, length(path) - 1)
                   WHERE NOT 'Passage_Info' IN labels(nodes(path)[idx])
                     AND NOT 'Person_Nodes' IN labels(nodes(path)[idx])
                 )
               )
            WITH collect(DISTINCT root) AS roots, collect(DISTINCT path) AS paths
            WITH roots, [path IN paths WHERE path IS NOT NULL] AS paths
            WITH roots + reduce(node_acc = [], path IN paths | node_acc + nodes(path)) AS node_rows,
                 reduce(rel_acc = [], path IN paths | rel_acc + relationships(path)) AS rel_rows
            UNWIND node_rows AS graph_node
            WITH collect(DISTINCT graph_node) AS graph_nodes, rel_rows
            UNWIND CASE WHEN rel_rows = [] THEN [null] ELSE rel_rows END AS graph_rel
            WITH graph_nodes,
                 [rel IN collect(DISTINCT graph_rel) WHERE rel IS NOT NULL] AS graph_rels
            RETURN
              [node IN graph_nodes | {{
                element_id: elementId(node),
                labels: labels(node),
                properties: properties(node)
              }}] AS nodes,
              [rel IN graph_rels | {{
                element_id: elementId(rel),
                start_element_id: elementId(startNode(rel)),
                end_element_id: elementId(endNode(rel)),
                type: type(rel),
                properties: properties(rel)
              }}] AS edges
            """,
            parameters={"person_ids": normalized_person_ids},
        )
        record = (result.get("records") or [{}])[0]
        raw_nodes = record.get("nodes") or []
        raw_edges = record.get("edges") or []

        element_to_node_id: dict[str, str] = {}
        nodes: dict[str, dict[str, Any]] = {}
        for raw_node in raw_nodes:
            labels = [str(label) for label in (raw_node.get("labels") or [])]
            properties = dict(raw_node.get("properties") or {})
            element_id = str(raw_node.get("element_id") or "")
            node_id = self._graph_node_id(labels, properties, element_id)
            if (
                "Person_Nodes" in labels
                and properties.get("person_id") in normalized_person_ids
            ):
                properties["review_focus"] = True
            element_to_node_id[element_id] = node_id
            node_type = labels[0] if labels else "Node"
            nodes[node_id] = {
                "id": node_id,
                "label": self._graph_node_label(labels, properties, element_id),
                "type": node_type,
                "properties": properties,
            }

        edges: dict[str, dict[str, Any]] = {}
        for raw_edge in raw_edges:
            source = element_to_node_id.get(str(raw_edge.get("start_element_id") or ""))
            target = element_to_node_id.get(str(raw_edge.get("end_element_id") or ""))
            if not source or not target:
                continue
            label = str(raw_edge.get("type") or "")
            properties = dict(raw_edge.get("properties") or {})
            properties.pop("level", None)
            codes = properties.get("codes") or []
            if label == "person_relation" and isinstance(codes, list) and codes:
                label = ",".join(str(code) for code in codes)
            edge_id = str(raw_edge.get("element_id") or f"{source}__{target}__{label}")
            edges[edge_id] = {
                "source": source,
                "target": target,
                "label": label,
                "properties": properties,
            }

        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    # ---------- 业务封装：同名人物人工审核 ----------

    def list_pending_identity_reviews(self) -> dict:
        """列出所有待人工审核的"可能同人"关系。"""

        return self.run_read_query(
            cypher="""
            MATCH (source:Person_Nodes)-[r:可能同人]->(target:Person_Nodes)
            OPTIONAL MATCH (source)-[:在文章中]->(sp:Passage_Info)
            OPTIONAL MATCH (target)-[:在文章中]->(tp:Passage_Info)
            RETURN source.person_id AS source_person_id,
                   source.name AS source_name,
                   target.person_id AS target_person_id,
                   target.name AS target_name,
                   r.confidence AS confidence,
                   r.reason AS reason,
                   r.evidence AS evidence,
                   collect(DISTINCT sp.title) AS source_passages,
                   collect(DISTINCT tp.title) AS target_passages
            ORDER BY r.confidence DESC
            """,
        )

    def adjudicate_identity(
        self,
        source_person_id: int,
        target_person_id: int,
        decision: str,
    ) -> dict:
        """执行身份裁定：merge（合并）或 keep_separate（保持独立）。"""

        if decision == "merge":
            merge_result = self.merge_person_nodes(
                canonical_person_id=target_person_id,
                duplicate_person_id=source_person_id,
            )
            # 合并后删除可能同人关系（若节点还存在的话）
            try:
                self.run_write_query(
                    cypher="""
                    MATCH (s:Person_Nodes {person_id: $source})-[r:可能同人]-(t:Person_Nodes {person_id: $target})
                    DELETE r
                    RETURN count(r) AS deleted_count
                    """,
                    parameters={"source": source_person_id, "target": target_person_id},
                )
            except Exception:
                pass  # 合并后节点已删，关系自动消失
            return {
                "status": "success",
                "action": "merge",
                "merge_result": merge_result,
            }

        # keep_separate
        del_result = self.run_write_query(
            cypher="""
            MATCH (s:Person_Nodes {person_id: $source})-[r:可能同人]-(t:Person_Nodes {person_id: $target})
            DELETE r
            RETURN count(r) AS deleted_count
            """,
            parameters={"source": source_person_id, "target": target_person_id},
        )
        return {
            "status": "success",
            "action": "keep_separate",
            "deleted_count": (del_result.get("records") or [{}])[0].get("deleted_count", 0),
        }

    def list_all_same_name_pairs(self) -> dict:
        """列出所有同名 Person_Nodes 对（用于标注模式）。"""

        return self.run_read_query(
            cypher="""
            MATCH (a:Person_Nodes), (b:Person_Nodes)
            WHERE a.name = b.name
              AND a.name IS NOT NULL
              AND trim(a.name) <> ''
              AND a.person_id < b.person_id
            OPTIONAL MATCH (a)-[:在文章中]->(sp:Passage_Info)
            OPTIONAL MATCH (b)-[:在文章中]->(tp:Passage_Info)
            OPTIONAL MATCH (a)-[review:可能同人]->(b)
            WITH a, b,
                 coalesce(review.confidence, 0) AS confidence,
                 coalesce(review.reason, '') AS reason,
                 review.evidence AS evidence,
                 collect(DISTINCT sp.title) AS source_passages,
                 collect(DISTINCT tp.title) AS target_passages
            RETURN a.person_id AS source_person_id,
                   a.name AS source_name,
                   b.person_id AS target_person_id,
                   b.name AS target_name,
                   confidence,
                   reason,
                   evidence,
                   source_passages,
                   target_passages
            ORDER BY a.name, a.person_id
            """,
        )

    def get_person_passage_doc_ids(self, person_id: int) -> list[int]:
        """获取人物关联的所有 Passage doc_id。"""

        result = self.run_read_query(
            cypher="""
            MATCH (p:Person_Nodes {person_id: $person_id})-[:在文章中]->(pa:Passage_Info)
            RETURN pa.doc_id AS doc_id
            """,
            parameters={"person_id": person_id},
        )
        return [r["doc_id"] for r in result.get("records", []) if r.get("doc_id") is not None]

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
                new_rel.note = coalesce(new_rel.note, r.note),
                new_rel.evidence = coalesce(new_rel.evidence, r.evidence),
                new_rel.source_doc_id = coalesce(new_rel.source_doc_id, r.source_doc_id),
                new_rel.direction_verified =
                    coalesce(new_rel.direction_verified, false) OR
                    coalesce(r.direction_verified, false)
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (keep:Person_Nodes {person_id: $canonical_person_id})
            MATCH (source:Person_Nodes)-[r:person_relation]->(drop:Person_Nodes {person_id: $duplicate_person_id})
            WHERE source.person_id <> $canonical_person_id
              AND source.person_id <> $duplicate_person_id
            MERGE (source)-[new_rel:person_relation]->(keep)
            SET new_rel.codes = coalesce(new_rel.codes, r.codes),
                new_rel.note = coalesce(new_rel.note, r.note),
                new_rel.evidence = coalesce(new_rel.evidence, r.evidence),
                new_rel.source_doc_id = coalesce(new_rel.source_doc_id, r.source_doc_id),
                new_rel.direction_verified =
                    coalesce(new_rel.direction_verified, false) OR
                    coalesce(r.direction_verified, false)
            RETURN count(new_rel) AS transferred
            """,
            """
            MATCH (drop:Person_Nodes {person_id: $duplicate_person_id})
            DETACH DELETE drop
            RETURN $duplicate_person_id AS deleted_person_id
            """,
        ]

        def _merge_tx(tx) -> list[dict[str, Any]]:
            tx_step_results: list[dict[str, Any]] = []
            for cypher in steps:
                actual_cypher, query_parameters = self._extract_query(cypher, parameters)
                result = tx.run(actual_cypher, query_parameters)
                records = [record.data() for record in result]
                summary = result.consume()
                tx_step_results.append(
                    {
                        "status": "success",
                        "operation": "write",
                        "cypher": actual_cypher,
                        "parameters": query_parameters,
                        "record_count": len(records),
                        "records": records,
                        "summary": self._build_summary(summary),
                    }
                )
            return tx_step_results

        driver = self.client.get_driver()
        with driver.session(database=self.client.settings.neo4j_database) as session:
            if hasattr(session, "execute_write"):
                step_results = session.execute_write(_merge_tx)
            else:  # pragma: no cover - compatibility for older neo4j drivers
                step_results = session.write_transaction(_merge_tx)

        return {
            "status": "success",
            "canonical_person_id": canonical_person_id,
            "duplicate_person_id": duplicate_person_id,
            "step_count": len(step_results),
            "steps": step_results,
        }
