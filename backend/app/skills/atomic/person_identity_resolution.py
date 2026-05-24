"""Function B: LLM-assisted identity resolution for same-name people."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.context import ExecutionContext
from app.graph.repository import GraphRepository
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured

Decision = Literal["same", "different", "insufficient"]
Focus = Literal["relations", "official_titles", "locations", "events"]

_DEFAULT_FOCUS: list[Focus] = ["relations", "official_titles", "locations", "events"]
_CONFIDENCE_THRESHOLD = 0.82


class IdentityResolutionDecision(BaseModel):
    decision: Decision
    confidence: float = Field(ge=0, le=1)
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_hop_focus: list[Focus] = Field(default_factory=list)
    reason: str = ""


class PersonIdentityResolutionAtomicSkill(BaseSkill):
    """Resolve same-name person candidates before merging graph nodes."""

    code = "person_identity_resolution_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        passage = context.metadata.get("passage", {})
        doc_id = passage.get("doc_id") or arguments.get("doc_id")
        if doc_id is None:
            raise ValueError("person identity resolution requires doc_id.")

        repo = GraphRepository()
        warnings = context.metadata.setdefault("warnings", [])

        try:
            candidates = repo.find_same_name_person_candidates_for_passage(int(doc_id))
        except Exception as exc:
            output = _empty_output(int(doc_id), status="failed")
            output["failures"].append({"error": str(exc)})
            warnings.append(
                {
                    "type": "function_b_candidate_recall_failed",
                    "detail": str(exc),
                }
            )
            context.metadata["person_identity_resolution"] = output
            return output

        records = candidates.get("records", [])
        if not records:
            output = _empty_output(int(doc_id), status="skipped")
            context.metadata["person_identity_resolution"] = output
            return output

        cases: list[dict[str, Any]] = []
        merges: list[dict[str, Any]] = []
        review_links: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        merged_duplicate_ids: set[int] = set()

        for candidate in records:
            new_person_id = int(candidate["new_person_id"])
            candidate_person_id = int(candidate["candidate_person_id"])
            if new_person_id in merged_duplicate_ids:
                continue

            case_result = self._resolve_one_pair(
                repo=repo,
                doc_id=int(doc_id),
                candidate=candidate,
                warnings=warnings,
                trace_metadata=context.metadata,
            )
            cases.append(case_result)

            if case_result["action"] == "merged":
                merged_duplicate_ids.add(new_person_id)
                merges.append(case_result)
            elif case_result["action"] == "manual_review":
                review_links.append(case_result)
            elif case_result["action"] == "failed":
                failures.append(case_result)

            # Once the current node has been merged, subsequent same-name
            # candidates would point at a deleted duplicate.
            if new_person_id in merged_duplicate_ids:
                continue

        status = "partial" if failures else "success"
        output = {
            "status": status,
            "doc_id": int(doc_id),
            "candidate_pair_count": len(records),
            "adjudicated_pair_count": len(cases),
            "merged_person_count": len(merges),
            "review_link_count": len(review_links),
            "failed_resolution_count": len(failures),
            "confidence_threshold": _CONFIDENCE_THRESHOLD,
            "cases": cases,
            "merges": merges,
            "review_links": review_links,
            "failures": failures,
        }
        if failures:
            warnings.append(
                {
                    "type": "function_b_identity_resolution_partial",
                    "detail": f"{len(failures)} identity resolution case(s) failed.",
                }
            )

        context.metadata["person_identity_resolution"] = output
        return output

    def _resolve_one_pair(
        self,
        repo: GraphRepository,
        doc_id: int,
        candidate: dict[str, Any],
        warnings: list[dict[str, Any]],
        trace_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        new_person_id = int(candidate["new_person_id"])
        candidate_person_id = int(candidate["candidate_person_id"])
        name = candidate.get("name")
        hop = 2
        focus: list[Focus] = []
        decisions: list[dict[str, Any]] = []

        while hop <= 5:
            try:
                new_evidence = repo.get_person_evidence_bundle(
                    person_id=new_person_id,
                    max_hops=hop,
                    focus=focus,
                )
                candidate_evidence = repo.get_person_evidence_bundle(
                    person_id=candidate_person_id,
                    max_hops=hop,
                    focus=focus,
                )
            except Exception as exc:
                return _failed_case(candidate, hop, f"evidence_fetch_failed: {exc}")

            try:
                decision = _call_identity_llm(
                    doc_id=doc_id,
                    name=str(name or ""),
                    hop=hop,
                    focus=focus,
                    new_evidence=new_evidence,
                    candidate_evidence=candidate_evidence,
                    trace_metadata=trace_metadata,
                )
            except LLMStructuredError as exc:
                warnings.append(
                    {
                        "type": "function_b_identity_llm_failed",
                        "detail": f"{name or new_person_id}/{candidate_person_id}: {exc}",
                    }
                )
                decision = IdentityResolutionDecision(
                    decision="insufficient",
                    confidence=0,
                    missing_evidence=["LLM did not return valid adjudication JSON."],
                    next_hop_focus=_DEFAULT_FOCUS,
                    reason=str(exc),
                )

            decision_payload = decision.model_dump()
            decisions.append({"hop": hop, "focus": list(focus), "decision": decision_payload})

            if decision.decision == "same" and decision.confidence >= _CONFIDENCE_THRESHOLD:
                try:
                    merge_result = repo.merge_person_nodes(
                        canonical_person_id=candidate_person_id,
                        duplicate_person_id=new_person_id,
                    )
                except Exception as exc:
                    return _failed_case(candidate, hop, f"merge_failed: {exc}", decisions)

                return {
                    **_case_base(candidate),
                    "action": "merged",
                    "final_decision": decision_payload,
                    "hops_used": hop,
                    "decision_trace": decisions,
                    "merge_result": merge_result,
                }

            if decision.decision == "different":
                return {
                    **_case_base(candidate),
                    "action": "kept_separate",
                    "final_decision": decision_payload,
                    "hops_used": hop,
                    "decision_trace": decisions,
                }

            next_focus = decision.next_hop_focus or _DEFAULT_FOCUS
            if hop >= 5:
                review = _mark_review(
                    repo=repo,
                    candidate=candidate,
                    decision=decision_payload,
                    hop=hop,
                )
                if review.get("status") == "failed":
                    error = f"review_link_failed: {review.get('error', 'unknown error')}"
                    warnings.append(
                        {
                            "type": "function_b_review_link_failed",
                            "detail": f"{name or new_person_id}/{candidate_person_id}: {error}",
                        }
                    )
                    return _failed_case(candidate, hop, error, decisions)
                return {
                    **_case_base(candidate),
                    "action": "manual_review",
                    "final_decision": decision_payload,
                    "hops_used": hop,
                    "decision_trace": decisions,
                    "review_result": review,
                }

            focus = list(dict.fromkeys(next_focus))
            hop += 1

        return _failed_case(candidate, 5, "unreachable_resolution_state", decisions)


def _call_identity_llm(
    doc_id: int,
    name: str,
    hop: int,
    focus: list[Focus],
    new_evidence: dict[str, Any],
    candidate_evidence: dict[str, Any],
    trace_metadata: dict[str, Any],
) -> IdentityResolutionDecision:
    system_prompt = (
        "你是 ACGAgent 功能 B 的同名人物身份裁定器。"
        "只能根据输入的结构化证据判断两个人物节点是否为同一历史人物，"
        "不得引入外部知识，不得要求系统执行未注册动作。"
        "必须返回 JSON：decision=same|different|insufficient，"
        "confidence 为 0 到 1，positive_evidence/negative_evidence/missing_evidence 为字符串数组，"
        "next_hop_focus 只能包含 relations、official_titles、locations、events，reason 为简短理由。"
        "只有证据明确互相支持时才返回 same；有明显冲突时返回 different；"
        "证据不足或需要更多路径时返回 insufficient。"
    )
    user_payload = {
        "doc_id": doc_id,
        "same_name": name,
        "current_hop": hop,
        "current_focus": focus,
        "new_person_evidence": new_evidence,
        "candidate_person_evidence": candidate_evidence,
    }
    return call_llm_structured(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_payload, ensure_ascii=False),
        schema=IdentityResolutionDecision,
        skill_code="person_identity_resolution_atomic",
        additional_metadata=trace_metadata,
    )


def _mark_review(
    repo: GraphRepository,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    hop: int,
) -> dict[str, Any]:
    try:
        return repo.mark_possible_same_person(
            source_person_id=int(candidate["new_person_id"]),
            target_person_id=int(candidate["candidate_person_id"]),
            confidence=float(decision.get("confidence") or 0),
            reason=str(decision.get("reason") or ""),
            evidence={
                "decision": decision,
                "hops_used": hop,
                "name": candidate.get("name"),
            },
        )
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _empty_output(doc_id: int, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "doc_id": doc_id,
        "candidate_pair_count": 0,
        "adjudicated_pair_count": 0,
        "merged_person_count": 0,
        "review_link_count": 0,
        "failed_resolution_count": 0,
        "confidence_threshold": _CONFIDENCE_THRESHOLD,
        "cases": [],
        "merges": [],
        "review_links": [],
        "failures": [],
    }


def _case_base(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": candidate.get("name"),
        "new_person_id": int(candidate["new_person_id"]),
        "candidate_person_id": int(candidate["candidate_person_id"]),
        "new_passage_doc_id": candidate.get("new_passage_doc_id"),
        "candidate_passage_doc_id": candidate.get("candidate_passage_doc_id"),
        "latest_candidate_passage_doc_id": candidate.get("latest_candidate_passage_doc_id"),
        "candidate_passages": candidate.get("candidate_passages") or [],
        "candidate_count_for_new_person": candidate.get("candidate_count_for_new_person"),
    }


def _failed_case(
    candidate: dict[str, Any],
    hop: int,
    error: str,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        **_case_base(candidate),
        "action": "failed",
        "hops_used": hop,
        "decision_trace": decisions or [],
        "error": error,
    }
