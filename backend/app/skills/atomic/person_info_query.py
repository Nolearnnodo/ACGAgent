from __future__ import annotations

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill


class PersonInfoQueryAtomicSkill(BaseSkill):
    code = "person_info_query_atomic"
    allowed_roles = ["user", "admin"]

    def __init__(self) -> None:
        self.repository = GraphRepository()

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        name = str(arguments.get("person_name", "")).strip()
        if not name:
            raise ValueError("person_info_query requires person_name.")

        match_result = self.repository.run_read_query(
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
                "life_events": _fetch_life_events(self.repository, pid),
                "relations": _fetch_relations(self.repository, pid),
                "historical_events": _fetch_historical_events(self.repository, pid),
                "passages": _fetch_passages(self.repository, pid),
            }
            persons.append(person_data)

        count = len(persons)
        return {
            "found": True,
            "source": "graph",
            "person_data": persons[0] if count == 1 else persons,
            "person_count": count,
        }


def _fetch_life_events(repo: GraphRepository, pid) -> list[dict]:
    result = repo.run_read_query(
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


def _fetch_relations(repo: GraphRepository, pid) -> list[dict]:
    result = repo.run_read_query(
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


def _fetch_historical_events(repo: GraphRepository, pid) -> list[dict]:
    result = repo.run_read_query(
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


def _fetch_passages(repo: GraphRepository, pid) -> list[dict]:
    result = repo.run_read_query(
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
