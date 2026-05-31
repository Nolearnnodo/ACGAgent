"""清空 Neo4j 图谱 + SQLite 中的古籍与执行记录，方便重跑。

用法（在 backend/ 目录下）：

    python -m scripts.reset_all            # dry-run，只打印统计
    python -m scripts.reset_all --apply    # 真删

清空范围：
  Neo4j  — 全部节点和边（MATCH (n) DETACH DELETE n）
  SQLite — passages、execution_runs、execution_step_runs、planner_decisions、
           llm_call_logs、tool_call_logs、execution_trace_summaries
           不删 users、auth_sessions、conversations、messages、skill_definitions 等
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.graph.repository import GraphRepository  # noqa: E402
from app.models.execution import (  # noqa: E402
    ExecutionRun,
    ExecutionStepRun,
    PlannerDecisionRecord,
)
from app.models.observability import (  # noqa: E402
    ExecutionTraceSummary,
    LLMCallLog,
    ToolCallLog,
)
from app.models.passage import Passage  # noqa: E402


def _neo4j_stats(repo: GraphRepository) -> dict[str, int]:
    result = repo.run_read_query(
        cypher="""
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS cnt
        ORDER BY label
        """,
    )
    return {r["label"]: r["cnt"] for r in result.get("records", [])}


def _sqlite_counts(db) -> dict[str, int]:
    return {
        "passages": db.query(Passage).count(),
        "execution_runs": db.query(ExecutionRun).count(),
        "execution_step_runs": db.query(ExecutionStepRun).count(),
        "planner_decisions": db.query(PlannerDecisionRecord).count(),
        "llm_call_logs": db.query(LLMCallLog).count(),
        "tool_call_logs": db.query(ToolCallLog).count(),
        "execution_trace_summaries": db.query(ExecutionTraceSummary).count(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="清空图谱和古籍记录")
    parser.add_argument("--apply", action="store_true", help="真删；不加则 dry-run")
    args = parser.parse_args()

    repo = GraphRepository()

    # ===== 统计 =====
    print("=" * 50)
    print("Neo4j 当前节点")
    print("=" * 50)
    neo4j_stats = _neo4j_stats(repo)
    if not neo4j_stats:
        print("  （空）")
    else:
        for label, cnt in sorted(neo4j_stats.items()):
            print(f"  {label:<25s} {cnt:>6}")
        print(f"  {'总计':<25s} {sum(neo4j_stats.values()):>6}")

    print()
    print("=" * 50)
    print("SQLite 将清空的表")
    print("=" * 50)
    with SessionLocal() as db:
        sqlite_counts = _sqlite_counts(db)
    for table, cnt in sqlite_counts.items():
        print(f"  {table:<30s} {cnt:>6} 行")

    if not args.apply:
        print()
        print("这是 dry-run。如要真删，加 --apply 重跑。")
        return 0

    # ===== 真删 =====
    print()
    print("=== 清空 Neo4j ===")
    result = repo.run_write_query(cypher="MATCH (n) DETACH DELETE n")
    summary = result.get("summary", {})
    print(f"  nodes_deleted         = {summary.get('nodes_deleted', 0)}")
    print(f"  relationships_deleted = {summary.get('relationships_deleted', 0)}")

    print()
    print("=== 清空 SQLite ===")
    with SessionLocal() as db:
        for model in (
            ExecutionTraceSummary,
            LLMCallLog,
            ToolCallLog,
            ExecutionStepRun,
            ExecutionRun,
            PlannerDecisionRecord,
            Passage,
        ):
            count = db.query(model).delete()
            print(f"  {model.__tablename__:<30s} 删除 {count} 行")
        db.commit()

    print()
    print("完成。可以重跑 passage ingestion 了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
