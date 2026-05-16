"""Function B: exact-name person merge.

This atomic skill is intentionally conservative: it does not call LLMs, CBDB, or
expert-review workflows. It only asks Neo4j to merge people from the current
passage into earlier people whose ``name`` property is exactly equal.
"""

from __future__ import annotations

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill


class PersonExactMatchMergeAtomicSkill(BaseSkill):
    """Merge current-passage people into earlier same-name people."""

    code = "person_exact_match_merge_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        doc_id = passage.get("doc_id") or arguments.get("doc_id")
        if doc_id is None:
            raise ValueError("person exact-name merge requires doc_id.")

        person_layer = context.metadata.get("person_layer", {})
        names = sorted(
            {
                str(person.get("name", "")).strip()
                for person in person_layer.get("persons", [])
                if str(person.get("name", "")).strip()
            }
        )
        if not names:
            output = {
                "status": "skipped",
                "doc_id": int(doc_id),
                "candidate_name_count": 0,
                "matched_person_count": 0,
                "merged_person_count": 0,
                "failed_merge_count": 0,
                "merges": [],
                "failures": [],
            }
            context.metadata["person_exact_match_merge"] = output
            return output

        warnings = context.metadata.setdefault("warnings", [])
        try:
            merge_result = GraphRepository().merge_same_name_persons_for_passage(
                doc_id=int(doc_id)
            )
        except Exception as exc:
            output = {
                "status": "failed",
                "doc_id": int(doc_id),
                "candidate_name_count": len(names),
                "matched_person_count": 0,
                "merged_person_count": 0,
                "failed_merge_count": 1,
                "merges": [],
                "failures": [{"error": str(exc)}],
            }
            warnings.append(
                {
                    "type": "function_b_exact_merge_failed",
                    "detail": str(exc),
                }
            )
            context.metadata["person_exact_match_merge"] = output
            return output

        output = {
            "candidate_name_count": len(names),
            **merge_result,
        }
        if output.get("failed_merge_count"):
            warnings.append(
                {
                    "type": "function_b_exact_merge_partial",
                    "detail": f"{output['failed_merge_count']} same-name merge(s) failed.",
                }
            )

        context.metadata["person_exact_match_merge"] = output
        return output
