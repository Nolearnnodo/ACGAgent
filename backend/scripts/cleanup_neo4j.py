"""清理 Neo4j 中指定 doc_id 的 passage 及其关联节点。

默认 dry-run：只打印将要删的内容，不真改库。
加 --apply 才真删。

会删：
  - Passage_Info（按 doc_id）
  - Person_Nodes（person_id 落在 [doc_id*1000, (doc_id+1)*1000) 区间内）
  - Life_Events（属于这些 Person）
  - 这些节点关联的所有边

不会删（共享节点，可能仍被其他 passage 引用）：
  - Time / Location / Official_title / Historical_Events
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.graph.repository import GraphRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按 doc_id 清理 Neo4j 中的 passage")
    parser.add_argument(
        "doc_ids",
        nargs="+",
        type=int,
        help="要删除的 doc_id 列表（空格分隔）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="不加该开关只做 dry-run；加上才真删",
    )
    args = parser.parse_args()

    bad_ids = sorted(set(args.doc_ids))
    repo = GraphRepository()

    # ===== Dry-run preview =====
    print(f"目标 doc_ids: {bad_ids}")
    preview = repo.run_read_query(
        cypher="""
        UNWIND $ids AS id
        OPTIONAL MATCH (p:Passage_Info {doc_id: id})
        OPTIONAL MATCH (p)<-[:在文章中]-(n:Person_Nodes)
        OPTIONAL MATCH (n)-[:生平]->(le:Life_Events)
        RETURN id AS doc_id,
               p.title AS title,
               count(DISTINCT n) AS persons,
               count(DISTINCT le) AS life_events
        ORDER BY id
        """,
        parameters={"ids": bad_ids},
    )

    print()
    print("=== 即将删除的内容 ===")
    total_passages = 0
    total_persons = 0
    total_events = 0
    for rec in preview["records"]:
        title = rec.get("title") or "(图中没有此 doc_id)"
        persons = rec.get("persons") or 0
        events = rec.get("life_events") or 0
        present = "✓" if rec.get("title") else "—"
        print(
            f"  {present} doc_id={rec['doc_id']:<8} persons={persons:>3} "
            f"life_events={events:>3}  {title}"
        )
        if rec.get("title"):
            total_passages += 1
            total_persons += persons
            total_events += events

    print()
    print(
        f"汇总：{total_passages} 篇 Passage_Info / "
        f"{total_persons} 个 Person_Nodes / "
        f"{total_events} 个 Life_Events 将被删除"
    )

    if not args.apply:
        print()
        print("这是 dry-run。如要真删，加 --apply 重跑。")
        return 0

    # ===== 真删 =====
    print()
    print("=== 开始真删 ===")
    result = repo.run_write_query(
        cypher="""
        UNWIND $ids AS id
        OPTIONAL MATCH (p:Passage_Info {doc_id: id})
        OPTIONAL MATCH (p)<-[:在文章中]-(n:Person_Nodes)
        OPTIONAL MATCH (n)-[:生平]->(le:Life_Events)
        DETACH DELETE p, n, le
        """,
        parameters={"ids": bad_ids},
    )
    summary = result.get("summary", {})
    print(
        f"  nodes_deleted         = {summary.get('nodes_deleted', 0)}\n"
        f"  relationships_deleted = {summary.get('relationships_deleted', 0)}"
    )
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
