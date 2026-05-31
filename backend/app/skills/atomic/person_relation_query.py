from __future__ import annotations

import time
from typing import Any

from app.agents.context import ExecutionContext
from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.observability.trace_repository import record_tool_call
from app.skills.base import BaseSkill

_LOOKUP_CYPHER = (
    "MATCH (p:Person_Nodes {name: $name}) "
    "RETURN p.person_id AS person_id, p.name AS name, p.zi AS zi, p.titles AS titles"
)

_DIRECT_RELATION_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[r:person_relation]->(b:Person_Nodes {person_id: $pid_b})
RETURN r.codes AS codes, r.note AS note, "a_to_b" AS direction
UNION
MATCH (b:Person_Nodes {person_id: $pid_b})-[r:person_relation]->(a:Person_Nodes {person_id: $pid_a})
RETURN r.codes AS codes, r.note AS note, "b_to_a" AS direction
"""

_SHARED_TIME_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[:生平]->(le_a:Life_Events)-[:发生于]->(t:Time)<-[:发生于]-(le_b:Life_Events)<-[:生平]-(b:Person_Nodes {person_id: $pid_b})
RETURN t.era AS era, t.year AS year, collect(DISTINCT le_a.event_type) + collect(DISTINCT le_b.event_type) AS event_types
"""

_SHARED_LOCATION_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[:生平]->(le_a:Life_Events)-[:发生于]->(l:Location)<-[:发生于]-(le_b:Life_Events)<-[:生平]-(b:Person_Nodes {person_id: $pid_b})
RETURN l AS location, collect(DISTINCT le_a.event_type) + collect(DISTINCT le_b.event_type) AS event_types
"""

_SHARED_TITLE_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[:生平]->(:Life_Events)-[:担任]->(o:Official_title)<-[:担任]-(:Life_Events)<-[:生平]-(b:Person_Nodes {person_id: $pid_b})
RETURN o.official_title AS title
"""

_SHARED_HIST_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[:历史事件]->(h:Historical_Events)<-[:历史事件]-(b:Person_Nodes {person_id: $pid_b})
RETURN h.event_name AS event_name
"""

_SHARED_PASSAGE_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[:在文章中]->(pa:Passage_Info)<-[:在文章中]-(b:Person_Nodes {person_id: $pid_b})
RETURN pa.doc_id AS doc_id, pa.title AS title
"""

_ALL_RELATIONS_CYPHER = """
MATCH (a:Person_Nodes {person_id: $pid_a})-[r:person_relation]->(other:Person_Nodes)
RETURN other.name AS target_name, r.codes AS codes, r.note AS note, "outgoing" AS direction
UNION
MATCH (other:Person_Nodes)-[r:person_relation]->(a:Person_Nodes {person_id: $pid_a})
RETURN other.name AS target_name, r.codes AS codes, r.note AS note, "incoming" AS direction
"""


def _run_traced_read_query(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    cypher: str,
    parameters: dict[str, Any],
) -> dict:
    started_at = time.time()
    input_data = {"cypher": cypher, "parameters": parameters}
    should_trace = bool(trace_context.get("execution_run_id"))
    try:
        result = repo.run_read_query(cypher, parameters)
    except Exception as exc:
        if should_trace:
            latency_ms = int((time.time() - started_at) * 1000)
            with SessionLocal() as db:
                record_tool_call(
                    db=db,
                    trace_context=trace_context,
                    tool_name="neo4j_read_query",
                    input_data=input_data,
                    output_data={},
                    latency_ms=latency_ms,
                    status="failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
        raise

    if not should_trace:
        return result

    latency_ms = int((time.time() - started_at) * 1000)
    with SessionLocal() as db:
        record_tool_call(
            db=db,
            trace_context=trace_context,
            tool_name="neo4j_read_query",
            input_data=input_data,
            output_data=result,
            latency_ms=latency_ms,
            status=str(result.get("status") or "success"),
            error_message=str(result.get("error") or ""),
        )
    return result


def _lookup_person(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    name: str,
) -> dict:
    result = _run_traced_read_query(repo, trace_context, _LOOKUP_CYPHER, {"name": name})
    records = result.get("records", [])
    if not records:
        return {"found": False, "source": "llm", "data": None}
    # use record with lowest person_id when multiple matches exist
    best = min(records, key=lambda r: r["person_id"])
    return {"found": True, "source": "graph", "data": best}


class PersonRelationQueryAtomicSkill(BaseSkill):
    code = "person_relation_query_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        person_a = arguments.get("person_a", "").strip()
        person_b = arguments.get("person_b", "").strip()
        trace_context = {**context.metadata, "skill_code": self.code}

        info_a = (
            _lookup_person(self.repository, trace_context, person_a)
            if person_a
            else {"found": False, "source": "llm", "data": None}
        )

        # 单人模式：只有 person_a，查询其所有关系
        if not person_b:
            if not info_a["found"]:
                return {
                    "person_a_info": info_a,
                    "person_b_info": {"found": False, "source": "llm", "data": None},
                    "direct_relations": [],
                    "all_relations": [],
                    "source": "llm",
                }
            pid_a = info_a["data"]["person_id"]
            all_relations = _run_traced_read_query(
                self.repository,
                trace_context,
                _ALL_RELATIONS_CYPHER,
                {"pid_a": pid_a},
            )["records"]
            return {
                "person_a_info": info_a,
                "person_b_info": {"found": False, "source": "llm", "data": None},
                "direct_relations": [],
                "all_relations": all_relations,
                "source": "graph",
            }

        # 双人模式：查询两人之间的关系
        info_b = _lookup_person(self.repository, trace_context, person_b)

        direct_relations: list[dict] = []
        shared_time: list[dict] = []
        shared_location: list[dict] = []
        shared_official_title: list[dict] = []
        shared_historical_events: list[dict] = []
        shared_passages: list[dict] = []

        if info_a["found"] and info_b["found"]:
            pid_a = info_a["data"]["person_id"]
            pid_b = info_b["data"]["person_id"]
            params = {"pid_a": pid_a, "pid_b": pid_b}

            direct_relations = _run_traced_read_query(
                self.repository,
                trace_context,
                _DIRECT_RELATION_CYPHER,
                params,
            )["records"]

            shared_time = _run_traced_read_query(
                self.repository,
                trace_context,
                _SHARED_TIME_CYPHER,
                params,
            )["records"]

            shared_location = _run_traced_read_query(
                self.repository,
                trace_context,
                _SHARED_LOCATION_CYPHER,
                params,
            )["records"]

            shared_official_title = _run_traced_read_query(
                self.repository,
                trace_context,
                _SHARED_TITLE_CYPHER,
                params,
            )["records"]

            shared_historical_events = _run_traced_read_query(
                self.repository,
                trace_context,
                _SHARED_HIST_CYPHER,
                params,
            )["records"]

            shared_passages = _run_traced_read_query(
                self.repository,
                trace_context,
                _SHARED_PASSAGE_CYPHER,
                params,
            )["records"]

        return {
            "person_a_info": info_a,
            "person_b_info": info_b,
            "direct_relations": direct_relations,
            "shared_time": shared_time,
            "shared_location": shared_location,
            "shared_official_title": shared_official_title,
            "shared_historical_events": shared_historical_events,
            "shared_passages": shared_passages,
            "source": "graph" if info_a["found"] else "llm",
        }
