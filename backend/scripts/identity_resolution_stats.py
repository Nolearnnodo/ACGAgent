"""统计功能 B 同名消歧的跳数分布。

用法（在 backend/ 目录下）：

    python -m scripts.identity_resolution_stats

输出：
  1. 各跳数判定的人物对数量（合并 / 排除 / 人工审核）
  2. 5 跳仍无法判定的人物 person_id 和姓名列表
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from app.db.session import SessionLocal
from app.models.execution import ExecutionStepRun


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

    if not rows:
        print("未找到 identity resolution 步骤记录。")
        return

    hop_counter: Counter[int] = Counter()
    action_by_hop: dict[int, Counter[str]] = {}
    unresolved: list[dict] = []

    for row in rows:
        try:
            output = json.loads(row.output_json)
        except (json.JSONDecodeError, TypeError):
            continue

        for case in output.get("cases", []):
            hops = case.get("hops_used")
            action = case.get("action", "unknown")
            if hops is None:
                continue

            hop_counter[hops] += 1
            action_by_hop.setdefault(hops, Counter())[action] += 1

            if action == "manual_review":
                unresolved.append(
                    {
                        "new_person_id": case.get("new_person_id"),
                        "candidate_person_id": case.get("candidate_person_id"),
                        "name": case.get("name"),
                        "hops_used": hops,
                        "confidence": (case.get("final_decision") or {}).get("confidence"),
                    }
                )

    print("=" * 50)
    print("功能 B 同名消歧 — 跳数分布统计")
    print("=" * 50)

    for hop in sorted(hop_counter):
        total = hop_counter[hop]
        breakdown = action_by_hop.get(hop, Counter())
        parts = ", ".join(f"{a}={c}" for a, c in sorted(breakdown.items()))
        print(f"  {hop} 跳: {total} 对  ({parts})")

    total_pairs = sum(hop_counter.values())
    print(f"\n  总计: {total_pairs} 对")

    print()
    print("=" * 50)
    print("5 跳仍无法判定 → 待人工审核")
    print("=" * 50)

    if not unresolved:
        print("  （无）")
    else:
        for item in unresolved:
            conf = item["confidence"]
            conf_str = f"{conf:.2f}" if conf is not None else "N/A"
            print(
                f"  {item['name']:<8s}  "
                f"new={item['new_person_id']}  "
                f"candidate={item['candidate_person_id']}  "
                f"confidence={conf_str}"
            )
        print(f"\n  共 {len(unresolved)} 对待审核")


if __name__ == "__main__":
    sys.exit(main() or 0)
