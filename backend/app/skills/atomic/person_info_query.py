from __future__ import annotations

import time
from typing import Any

from app.agents.context import ExecutionContext
from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.observability.trace_repository import record_tool_call
from app.skills.base import BaseSkill


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
        result = repo.run_read_query(cypher=cypher, parameters=parameters)
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


class PersonInfoQueryAtomicSkill(BaseSkill):
    code = "person_info_query_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        name = str(arguments.get("person_name", "")).strip()
        if not name:
            raise ValueError("person_info_query requires person_name.")

        trace_context = {**context.metadata, "skill_code": self.code}
        match_result = _run_traced_read_query(
            self.repository,
            trace_context,
            cypher="MATCH (p:Person_Nodes {name: $name}) RETURN p",
            parameters={"name": name},
        )

        if match_result.get("status") != "success" or not match_result.get("records"):
            return {"found": False, "source": "llm", "person_data": None, "person_count": 0}

        persons = []
        for record in match_result["records"]:
            p = record["p"]
            pid = p["person_id"]
            person_data = {
                "person_id": pid,
                "name": p.get("name"),
                "zi": p.get("zi"),
                "titles": p.get("titles"),
                "life_events": _fetch_life_events(self.repository, trace_context, pid),
                "relations": _fetch_relations(self.repository, trace_context, pid),
                "historical_events": _fetch_historical_events(
                    self.repository,
                    trace_context,
                    pid,
                ),
                "passages": _fetch_passages(self.repository, trace_context, pid),
            }
            persons.append(person_data)

        count = len(persons)
        return {
            "found": True,
            "source": "graph",
            "person_data": persons[0] if count == 1 else persons,
            "person_count": count,
        }


def _fetch_life_events(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    pid,
) -> list[dict]:
    result = _run_traced_read_query(
        repo,
        trace_context,
        cypher=(
            "MATCH (p:Person_Nodes {person_id: $pid})-[:生平]->(le:Life_Events) "
            "OPTIONAL MATCH (le)-[:发生于]->(t:Time) "
            "OPTIONAL MATCH (le)-[:发生于]->(l:Location) "
            "OPTIONAL MATCH (le)-[:担任]->(o:Official_title) "
            "RETURN le, t, l, o"
        ),
        parameters={"pid": pid},
    )
    events = []
    for rec in result.get("records", []):
        le = rec.get("le") or {}
        t = rec.get("t")
        l = rec.get("l")
        o = rec.get("o")
        events.append({
            "event_id": le.get("event_id"),
            "event_type": le.get("event_type"),
            "time": {k: t[k] for k in ("era", "year", "month", "day") if k in t} if t else None,
            "location": {k: l[k] for k in ("dao", "fu", "zhou", "jun", "xian", "other") if k in l} if l else None,
            "official_title": o.get("official_title") if o else None,
        })
    return events


def _fetch_relations(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    pid,
) -> list[dict]:
    result = _run_traced_read_query(
        repo,
        trace_context,
        cypher=(
            "MATCH (p:Person_Nodes {person_id: $pid})-[r:person_relation]->(other:Person_Nodes) "
            "RETURN other.name AS target_name, r.codes AS codes, r.note AS note "
            "UNION "
            "MATCH (other:Person_Nodes)-[r:person_relation]->(p:Person_Nodes {person_id: $pid}) "
            "RETURN other.name AS target_name, r.codes AS codes, r.note AS note"
        ),
        parameters={"pid": pid},
    )
    return [
        {"target_name": rec.get("target_name"), "codes": rec.get("codes"), "note": rec.get("note")}
        for rec in result.get("records", [])
    ]


def _fetch_historical_events(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    pid,
) -> list[dict]:
    result = _run_traced_read_query(
        repo,
        trace_context,
        cypher=(
            "MATCH (p:Person_Nodes {person_id: $pid})-[r:历史事件]->(h:Historical_Events) "
            "RETURN h.event_name AS event_name, r.label AS label"
        ),
        parameters={"pid": pid},
    )
    return [
        {"event_name": rec.get("event_name"), "label": rec.get("label")}
        for rec in result.get("records", [])
    ]


def _fetch_passages(
    repo: GraphRepository,
    trace_context: dict[str, Any],
    pid,
) -> list[dict]:
    result = _run_traced_read_query(
        repo,
        trace_context,
        cypher=(
            "MATCH (p:Person_Nodes {person_id: $pid})-[r:在文章中]->(pa:Passage_Info) "
            "RETURN pa.doc_id AS doc_id, pa.title AS title, r.level AS level"
        ),
        parameters={"pid": pid},
    )
    return [
        {"doc_id": rec.get("doc_id"), "title": rec.get("title"), "level": rec.get("level")}
        for rec in result.get("records", [])
    ]
