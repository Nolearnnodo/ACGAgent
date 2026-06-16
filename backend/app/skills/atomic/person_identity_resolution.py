"""功能 B：同名人物增量证据裁定与全文终局裁定。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.context import ExecutionContext
from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.models.execution import IdentityResolutionDecisionLog
from app.models.passage import Passage
from app.skills.base import BaseSkill
from app.skills.common.llm_helper import LLMStructuredError, call_llm_structured

Decision = Literal["same", "different", "insufficient"]
Focus = Literal["relations", "official_titles", "locations", "events"]

_DEFAULT_FOCUS: list[Focus] = ["relations", "official_titles", "locations", "events"]
_CONFIDENCE_THRESHOLD = 0.82
_EVIDENCE_LIST_KEYS = (
    "passages",
    "life_events",
    "relations",
    "historical_events",
    "relation_paths",
    "related_person_evidence",
)


class IdentityResolutionDecision(BaseModel):
    decision: Decision
    confidence: float = Field(ge=0, le=1)
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_hop_focus: list[Focus] = Field(default_factory=list)
    reason: str = ""


class FullTextIdentityResolutionDecision(BaseModel):
    """全文终局裁定不再需要下一跳取证方向。"""

    decision: Decision
    confidence: float = Field(ge=0, le=1)
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class PersonIdentityResolutionAtomicSkill(BaseSkill):
    """同名候选召回、增量取证、逐轮留痕和安全合并。"""

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
        target_new_person_id = arguments.get("new_person_id")
        target_candidate_person_id = arguments.get("candidate_person_id")
        if (target_new_person_id is None) != (target_candidate_person_id is None):
            raise ValueError(
                "new_person_id and candidate_person_id must be provided together."
            )
        if target_new_person_id is not None:
            target_pair = (int(target_new_person_id), int(target_candidate_person_id))
            records = [
                candidate
                for candidate in records
                if (
                    int(candidate["new_person_id"]),
                    int(candidate["candidate_person_id"]),
                )
                == target_pair
            ]
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
            if new_person_id in merged_duplicate_ids:
                continue

            case_result = self._resolve_one_pair(
                repo=repo,
                doc_id=int(doc_id),
                candidate=candidate,
                current_passage=passage,
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
        current_passage: dict[str, Any],
        warnings: list[dict[str, Any]],
        trace_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        new_person_id = int(candidate["new_person_id"])
        candidate_person_id = int(candidate["candidate_person_id"])
        name = str(candidate.get("name") or "")
        focus: list[Focus] = []
        decisions: list[dict[str, Any]] = []
        cumulative_new = _empty_evidence(new_person_id)
        cumulative_candidate = _empty_evidence(candidate_person_id)

        for hop in range(2, 6):
            try:
                new_delta = repo.get_person_evidence_bundle(
                    person_id=new_person_id,
                    max_hops=hop,
                    focus=focus,
                    incremental=hop > 2,
                )
                candidate_delta = repo.get_person_evidence_bundle(
                    person_id=candidate_person_id,
                    max_hops=hop,
                    focus=focus,
                    incremental=hop > 2,
                )
            except Exception as exc:
                return _failed_case(candidate, hop, f"evidence_fetch_failed: {exc}", decisions)

            _merge_evidence(cumulative_new, new_delta)
            _merge_evidence(cumulative_candidate, candidate_delta)

            try:
                decision = _call_identity_llm(
                    doc_id=doc_id,
                    name=name,
                    hop=hop,
                    focus=focus,
                    new_evidence=cumulative_new,
                    candidate_evidence=cumulative_candidate,
                    new_evidence_delta=new_delta,
                    candidate_evidence_delta=candidate_delta,
                    prior_decisions=decisions,
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
            decision_record = {
                "round_type": "graph_evidence",
                "hop": hop,
                "focus": list(focus),
                "new_evidence_delta_counts": _evidence_counts(new_delta),
                "candidate_evidence_delta_counts": _evidence_counts(candidate_delta),
                "decision": decision_payload,
            }
            decisions.append(decision_record)
            _persist_decision(
                trace_metadata=trace_metadata,
                passage_id=doc_id,
                new_person_id=new_person_id,
                candidate_person_id=candidate_person_id,
                hop=hop,
                focus=focus,
                decision=decision,
                used_full_text=False,
                warnings=warnings,
            )

            terminal = _apply_terminal_decision(
                repo=repo,
                candidate=candidate,
                decision=decision,
                hop=hop,
                decisions=decisions,
                used_full_text=False,
            )
            if terminal is not None:
                return terminal

            focus = list(dict.fromkeys(decision.next_hop_focus or _DEFAULT_FOCUS))

        full_text_sources = _load_full_text_sources(
            new_doc_ids=_passage_doc_ids(cumulative_new),
            candidate_doc_ids=_passage_doc_ids(cumulative_candidate),
            current_passage=current_passage,
            warnings=warnings,
        )
        try:
            final_decision = _call_full_text_identity_llm(
                doc_id=doc_id,
                name=name,
                new_person_id=new_person_id,
                candidate_person_id=candidate_person_id,
                graph_decisions=decisions,
                new_person_sources=full_text_sources["new_person_sources"],
                candidate_person_sources=full_text_sources["candidate_person_sources"],
                trace_metadata=trace_metadata,
            )
        except LLMStructuredError as exc:
            warnings.append(
                {
                    "type": "function_b_full_text_llm_failed",
                    "detail": f"{name or new_person_id}/{candidate_person_id}: {exc}",
                }
            )
            final_decision = IdentityResolutionDecision(
                decision="insufficient",
                confidence=0,
                missing_evidence=["全文终局裁定未返回合法 JSON。"],
                reason=str(exc),
            )

        final_payload = final_decision.model_dump()
        decisions.append(
            {
                "round_type": "full_text_final",
                "hop": 5,
                "focus": [],
                "new_source_doc_ids": [
                    source["doc_id"] for source in full_text_sources["new_person_sources"]
                ],
                "candidate_source_doc_ids": [
                    source["doc_id"]
                    for source in full_text_sources["candidate_person_sources"]
                ],
                "decision": final_payload,
            }
        )
        _persist_decision(
            trace_metadata=trace_metadata,
            passage_id=doc_id,
            new_person_id=new_person_id,
            candidate_person_id=candidate_person_id,
            hop=5,
            focus=[],
            decision=final_decision,
            used_full_text=True,
            warnings=warnings,
        )

        terminal = _apply_terminal_decision(
            repo=repo,
            candidate=candidate,
            decision=final_decision,
            hop=5,
            decisions=decisions,
            used_full_text=True,
        )
        if terminal is not None:
            return terminal

        review = _mark_review(
            repo=repo,
            candidate=candidate,
            decision=final_payload,
            hop=5,
            decision_trace=decisions,
        )
        if review.get("status") == "failed":
            error = f"review_link_failed: {review.get('error', 'unknown error')}"
            warnings.append(
                {
                    "type": "function_b_review_link_failed",
                    "detail": f"{name or new_person_id}/{candidate_person_id}: {error}",
                }
            )
            return _failed_case(candidate, 5, error, decisions)
        return {
            **_case_base(candidate),
            "action": "manual_review",
            "final_decision": final_payload,
            "hops_used": 5,
            "used_full_text": True,
            "decision_trace": decisions,
            "review_result": review,
        }


def _empty_evidence(person_id: int) -> dict[str, Any]:
    return {
        "person": {"person_id": person_id},
        **{key: [] for key in _EVIDENCE_LIST_KEYS},
        "max_hops": 0,
        "focus": [],
    }


def _stable_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _merge_evidence(cumulative: dict[str, Any], delta: dict[str, Any]) -> None:
    if delta.get("person"):
        cumulative["person"] = {**cumulative.get("person", {}), **delta["person"]}
    for key in _EVIDENCE_LIST_KEYS:
        existing = cumulative.setdefault(key, [])
        seen = {_stable_key(item) for item in existing}
        for item in delta.get(key) or []:
            marker = _stable_key(item)
            if marker not in seen:
                existing.append(item)
                seen.add(marker)
    cumulative["max_hops"] = max(
        int(cumulative.get("max_hops") or 0),
        int(delta.get("max_hops") or 0),
    )
    cumulative["focus"] = sorted(
        set(cumulative.get("focus") or []) | set(delta.get("focus") or [])
    )


def _evidence_counts(bundle: dict[str, Any]) -> dict[str, int]:
    return {key: len(bundle.get(key) or []) for key in _EVIDENCE_LIST_KEYS}


def _call_identity_llm(
    doc_id: int,
    name: str,
    hop: int,
    focus: list[Focus],
    new_evidence: dict[str, Any],
    candidate_evidence: dict[str, Any],
    new_evidence_delta: dict[str, Any],
    candidate_evidence_delta: dict[str, Any],
    prior_decisions: list[dict[str, Any]],
    trace_metadata: dict[str, Any],
) -> IdentityResolutionDecision:
    system_prompt = (
        "你是 ACGAgent 功能 B 的同名人物身份裁定器。"
        "只能根据输入的结构化证据判断两个人物节点是否为同一历史人物，"
        "不得引入外部知识。当前轮次提供服务端累计去重后的证据和本轮新增证据。"
        "必须返回 JSON：decision=same|different|insufficient，confidence 为 0 到 1，"
        "positive_evidence/negative_evidence/missing_evidence 为字符串数组，"
        "next_hop_focus 只能包含 relations、official_titles、locations、events，"
        "reason 为简短、可审计的判断依据，不要输出隐含思维过程。"
        "只有证据明确互相支持时才返回 same；有明显冲突时返回 different；"
        "证据不足时返回 insufficient。"
        "【关键规则】缺乏证据 ≠ 不同人。"
        "一方信息稀少不构成 different 的理由，只有存在明确冲突（如朝代不同、亲属互斥）时才可判 different。"
    )
    user_payload = {
        "doc_id": doc_id,
        "same_name": name,
        "current_hop": hop,
        "current_focus": focus,
        "current_round_delta": {
            "new_person": new_evidence_delta,
            "candidate_person": candidate_evidence_delta,
        },
        "cumulative_evidence": {
            "new_person": new_evidence,
            "candidate_person": candidate_evidence,
        },
        "prior_decisions": prior_decisions,
    }
    return call_llm_structured(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_payload, ensure_ascii=False),
        schema=IdentityResolutionDecision,
        skill_code="person_identity_resolution_atomic",
        additional_metadata={
            **trace_metadata,
            "purpose": f"identity_resolution_hop_{hop}",
        },
        max_prompt_chars=24000,
    )


def _call_full_text_identity_llm(
    doc_id: int,
    name: str,
    new_person_id: int,
    candidate_person_id: int,
    graph_decisions: list[dict[str, Any]],
    new_person_sources: list[dict[str, Any]],
    candidate_person_sources: list[dict[str, Any]],
    trace_metadata: dict[str, Any],
) -> IdentityResolutionDecision:
    system_prompt = (
        "你是 ACGAgent 功能 B 的同名人物全文终局裁定器。"
        "图证据经过 2 到 5 跳仍不能完成判断，现在提供两个人物全部来源文章的完整原文。"
        "只能依据这些原文和已记录的图证据裁定，不得引入外部知识。"
        "必须返回 JSON：decision=same|different|insufficient、confidence、"
        "positive_evidence、negative_evidence、missing_evidence、reason。"
        "这是最后一轮裁定，不要返回 next_hop_focus 或其他下一步取证字段。"
        "reason 只写简短、可审计的判断依据，不要输出隐含思维过程。"
        "若全文仍不足，必须返回 insufficient，不得猜测。\n"
        "【关键规则】缺乏证据 ≠ 不同人。"
        "当两人完全同名且无任何矛盾证据时，即使新人物信息稀少，也不得判定 different。"
        "只有存在明确冲突（如生卒年矛盾、朝代不同、亲属关系互斥等）时才可判 different。"
        "新人物仅在文中被简略提及、缺少详细信息，不构成 different 的理由，应判 insufficient。"
    )
    payload = {
        "doc_id": doc_id,
        "same_name": name,
        "new_person_id": new_person_id,
        "candidate_person_id": candidate_person_id,
        "graph_decision_trace": graph_decisions,
        "new_person_full_source_texts": new_person_sources,
        "candidate_person_full_source_texts": candidate_person_sources,
    }
    final_response = call_llm_structured(
        system_prompt=system_prompt,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        schema=FullTextIdentityResolutionDecision,
        skill_code="person_identity_resolution_atomic",
        additional_metadata={
            **trace_metadata,
            "purpose": "identity_resolution_full_text_final",
        },
        max_prompt_chars=None,
    )
    return IdentityResolutionDecision(
        **final_response.model_dump(exclude={"next_hop_focus"}),
        next_hop_focus=[],
    )


def _passage_doc_ids(evidence: dict[str, Any]) -> list[int]:
    return sorted(
        {
            int(item["doc_id"])
            for item in evidence.get("passages") or []
            if item.get("doc_id") is not None
        }
    )


def _load_full_text_sources(
    new_doc_ids: list[int],
    candidate_doc_ids: list[int],
    current_passage: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    all_doc_ids = sorted(set(new_doc_ids) | set(candidate_doc_ids))
    by_doc_id: dict[int, dict[str, Any]] = {}
    current_doc_id = current_passage.get("doc_id")
    if current_doc_id is not None and current_passage.get("context") is not None:
        by_doc_id[int(current_doc_id)] = {
            "doc_id": int(current_doc_id),
            "title": str(current_passage.get("title") or ""),
            "context": str(current_passage.get("context") or ""),
        }

    missing_ids = [doc_id for doc_id in all_doc_ids if doc_id not in by_doc_id]
    if missing_ids:
        try:
            with SessionLocal() as db:
                passages = db.query(Passage).filter(Passage.doc_id.in_(missing_ids)).all()
                for passage in passages:
                    by_doc_id[int(passage.doc_id)] = {
                        "doc_id": int(passage.doc_id),
                        "title": passage.title,
                        "context": passage.context,
                    }
        except Exception as exc:
            warnings.append(
                {
                    "type": "function_b_full_text_load_failed",
                    "detail": str(exc),
                }
            )

    unresolved = [doc_id for doc_id in all_doc_ids if doc_id not in by_doc_id]
    if unresolved:
        warnings.append(
            {
                "type": "function_b_full_text_missing",
                "detail": f"未找到完整原文的 doc_id: {unresolved}",
            }
        )

    return {
        "new_person_sources": [
            by_doc_id[doc_id] for doc_id in new_doc_ids if doc_id in by_doc_id
        ],
        "candidate_person_sources": [
            by_doc_id[doc_id] for doc_id in candidate_doc_ids if doc_id in by_doc_id
        ],
    }


def _persist_decision(
    trace_metadata: dict[str, Any],
    passage_id: int,
    new_person_id: int,
    candidate_person_id: int,
    hop: int,
    focus: list[Focus],
    decision: IdentityResolutionDecision,
    used_full_text: bool,
    warnings: list[dict[str, Any]],
) -> None:
    execution_run_id = trace_metadata.get("execution_run_id")
    if execution_run_id is None:
        return
    try:
        with SessionLocal() as db:
            db.add(
                IdentityResolutionDecisionLog(
                    execution_run_id=int(execution_run_id),
                    execution_step_run_id=trace_metadata.get("execution_step_run_id"),
                    passage_id=passage_id,
                    new_person_id=new_person_id,
                    candidate_person_id=candidate_person_id,
                    hop=hop,
                    focus_json=json.dumps(focus, ensure_ascii=False),
                    decision=decision.decision,
                    confidence=decision.confidence,
                    positive_evidence_json=json.dumps(
                        decision.positive_evidence,
                        ensure_ascii=False,
                    ),
                    negative_evidence_json=json.dumps(
                        decision.negative_evidence,
                        ensure_ascii=False,
                    ),
                    missing_evidence_json=json.dumps(
                        decision.missing_evidence,
                        ensure_ascii=False,
                    ),
                    next_hop_focus_json=json.dumps(
                        decision.next_hop_focus,
                        ensure_ascii=False,
                    ),
                    reason=decision.reason,
                    used_full_text=used_full_text,
                )
            )
            db.commit()
    except Exception as exc:
        warnings.append(
            {
                "type": "function_b_decision_log_failed",
                "detail": (
                    f"{new_person_id}/{candidate_person_id} hop={hop} "
                    f"used_full_text={used_full_text}: {exc}"
                ),
            }
        )


def _apply_terminal_decision(
    repo: GraphRepository,
    candidate: dict[str, Any],
    decision: IdentityResolutionDecision,
    hop: int,
    decisions: list[dict[str, Any]],
    used_full_text: bool,
) -> dict[str, Any] | None:
    from app.core.config import get_settings

    decision_payload = decision.model_dump()
    if decision.decision == "same" and decision.confidence >= _CONFIDENCE_THRESHOLD:
        if get_settings().identity_merge_disabled:
            review = _mark_review(
                repo=repo,
                candidate=candidate,
                decision=decision_payload,
                hop=hop,
                decision_trace=decisions,
            )
            return {
                **_case_base(candidate),
                "action": "manual_review",
                "final_decision": decision_payload,
                "hops_used": hop,
                "used_full_text": used_full_text,
                "decision_trace": decisions,
                "review_result": review,
                "merge_suppressed": True,
            }
        try:
            merge_result = repo.merge_person_nodes(
                canonical_person_id=int(candidate["candidate_person_id"]),
                duplicate_person_id=int(candidate["new_person_id"]),
            )
        except Exception as exc:
            return _failed_case(candidate, hop, f"merge_failed: {exc}", decisions)
        return {
            **_case_base(candidate),
            "action": "merged",
            "final_decision": decision_payload,
            "hops_used": hop,
            "used_full_text": used_full_text,
            "decision_trace": decisions,
            "merge_result": merge_result,
        }

    if decision.decision == "different":
        try:
            keep_separate_result = repo.adjudicate_identity(
                source_person_id=int(candidate["new_person_id"]),
                target_person_id=int(candidate["candidate_person_id"]),
                decision="keep_separate",
            )
        except Exception as exc:
            return _failed_case(
                candidate,
                hop,
                f"keep_separate_failed: {exc}",
                decisions,
            )
        return {
            **_case_base(candidate),
            "action": "kept_separate",
            "final_decision": decision_payload,
            "hops_used": hop,
            "used_full_text": used_full_text,
            "decision_trace": decisions,
            "keep_separate_result": keep_separate_result,
        }
    return None


def _mark_review(
    repo: GraphRepository,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    hop: int,
    decision_trace: list[dict[str, Any]],
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
                "used_full_text": True,
                "decision_trace": decision_trace,
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
