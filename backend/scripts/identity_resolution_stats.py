"""统计功能 B 同名消歧的跳数分布。

用法（在 backend/ 目录下）：

    python -m scripts.identity_resolution_stats

输出：
  1. LLM 首次返回 same/different 的跳数分布
  2. 当前待人工审核的人物对数量与明细
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.models.execution import ExecutionStepRun, IdentityResolutionDecisionLog
from app.models.observability import LLMCallLog


def _load_same_names_from_llm_calls(step_run_ids: list[int]) -> dict[tuple[int | None, int, int], str]:
    if not step_run_ids:
        return {}

    with SessionLocal() as db:
        calls = (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.execution_step_run_id.in_(step_run_ids),
                LLMCallLog.skill_code == "person_identity_resolution_atomic",
            )
            .all()
        )

    names: dict[tuple[int | None, int, int], str] = {}
    for call in calls:
        try:
            request = json.loads(call.request_json)
        except (json.JSONDecodeError, TypeError):
            continue

        for message in request.get("messages", []):
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue

            same_name = str(payload.get("same_name") or "").strip()
            new_person_id = payload.get("new_person_id")
            candidate_person_id = payload.get("candidate_person_id")
            if same_name and new_person_id is not None and candidate_person_id is not None:
                key = (call.passage_id, int(new_person_id), int(candidate_person_id))
                fallback_key = (None, int(new_person_id), int(candidate_person_id))
                names[key] = same_name
                names[fallback_key] = same_name
    return names


def main() -> None:
    with SessionLocal() as db:
        rows = (
            db.query(ExecutionStepRun)
            .filter(
                ExecutionStepRun.skill_code == "person_identity_resolution_atomic",
                ExecutionStepRun.status == "success",
            )
            .all()
        )
        step_run_ids = [row.id for row in rows]
        decision_logs = (
            db.query(IdentityResolutionDecisionLog)
            .filter(
                IdentityResolutionDecisionLog.execution_step_run_id.in_(step_run_ids),
            )
            .all()
            if step_run_ids
            else []
        )

    if not rows:
        print("未找到 identity resolution 步骤记录。")
        return

    logs_by_pair: dict[tuple[int | None, int, int], list[IdentityResolutionDecisionLog]] = {}
    for log in decision_logs:
        key = (log.passage_id, log.new_person_id, log.candidate_person_id)
        logs_by_pair.setdefault(key, []).append(log)

    hop_counter: Counter[int] = Counter()
    decision_by_hop: dict[int, Counter[str]] = {}
    for logs in logs_by_pair.values():
        for log in sorted(logs, key=lambda item: item.id):
            if log.decision not in ("same", "different"):
                continue
            hop_counter[log.hop] += 1
            decision_by_hop.setdefault(log.hop, Counter())[log.decision] += 1
            break

    pending_result = GraphRepository().list_pending_identity_reviews()
    pending_reviews = pending_result.get("records", [])
    llm_same_names = _load_same_names_from_llm_calls(step_run_ids)

    print("=" * 50)
    print("功能 B 同名消歧 — LLM 首次可判定跳数分布")
    print("=" * 50)

    for hop in sorted(hop_counter):
        total = hop_counter[hop]
        breakdown = decision_by_hop.get(hop, Counter())
        parts = ", ".join(
            f"{label}={breakdown[key]}"
            for key, label in (("same", "同人"), ("different", "非同人"))
            if breakdown.get(key)
        )
        print(f"  {hop} 跳: {total} 对  ({parts})")

    total_pairs = sum(hop_counter.values())
    print(f"\n  总计: {total_pairs} 对")

    print()
    print("=" * 50)
    print("当前待人工审核")
    print("=" * 50)

    if not pending_reviews:
        print("  （无）")
    else:
        for item in pending_reviews:
            source_id = int(item["source_person_id"])
            target_id = int(item["target_person_id"])
            key = (
                None,
                source_id,
                target_id,
            )
            source_name = str(item.get("source_name") or "").strip()
            target_name = str(item.get("target_name") or "").strip()
            name = source_name or target_name or llm_same_names.get(key) or "未知"
            if source_name and target_name and source_name != target_name:
                name = f"{source_name}/{target_name}"
            conf = item.get("confidence")
            conf_str = f"{conf:.2f}" if conf is not None else "N/A"
            reason = item.get("reason") or ""
            print(
                f"  {name:<8s}  "
                f"source={source_id}  "
                f"target={target_id}  "
                f"confidence={conf_str}  "
                f"reason={reason}"
            )
        print(f"\n  共 {len(pending_reviews)} 对待人工审核")


if __name__ == "__main__":
    sys.exit(main() or 0)
