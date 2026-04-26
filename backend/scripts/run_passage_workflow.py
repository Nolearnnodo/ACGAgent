"""批量跑功能 A 抽取流水线的正式入口工具。

用法（在仓库根目录或 backend/ 下都可）：

    # dry-run：不连真 Neo4j，便于本机或 CI 联调
    python -m scripts.run_passage_workflow test_data/ --dry-run

    # 正式跑：写真 Neo4j + 真 LLM
    python -m scripts.run_passage_workflow test_data/

输出：每篇文档产出一个 YAML 文件到 output/{epitaph|history}_{doc_id:04d}_{title}.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _patch_graph_repository_to_stub() -> None:
    """把 GraphRepository 替换为 noop stub，避免连真 Neo4j。"""

    import app.graph.repository as repo_module

    def _make_stub():
        stub = MagicMock()
        success = {"status": "success", "operation": "stub"}
        for method in [
            "upsert_passage_info",
            "upsert_person",
            "upsert_life_event",
            "upsert_historical_event_time_link",
            "upsert_person_historical_event",
            "upsert_person_relation",
            "run_read_query",
            "run_write_query",
        ]:
            getattr(stub, method).return_value = success
        return stub

    repo_module.GraphRepository = _make_stub
    for skill_module in (
        "app.skills.atomic.passage_meta",
        "app.skills.atomic.person_layer",
        "app.skills.atomic.event_relation",
    ):
        mod = __import__(skill_module, fromlist=["GraphRepository"])
        mod.GraphRepository = _make_stub  # type: ignore[attr-defined]


def _build_context(repo_root: Path, file_path: Path, doc_id: int, output_dir: Path):
    from app.agents.context import ExecutionContext

    text = file_path.read_text(encoding="utf-8")
    try:
        rel = file_path.relative_to(repo_root).as_posix()
    except ValueError:
        # 输入路径不在仓库内（如绝对路径在别处）：直接记原始路径
        rel = file_path.as_posix()
    return ExecutionContext(
        user={"id": 1, "role": "admin", "email": "admin@example.com"},
        conversation={},
        metadata={
            "trigger_type": "passage_upload",
            "passage_id": doc_id,
            "passage": {
                "doc_id": doc_id,
                "title": file_path.stem,
                "context": text,
                "source_type": "upload",
                "source_file": rel,
            },
            "output_dir": str(output_dir),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="批量跑功能 A 抽取流水线")
    parser.add_argument("input_path", help="单文件或目录（递归扫描 *.txt）")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="YAML 输出目录（默认仓库根 output/）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不连真 Neo4j。LLM 仍按 .env 配置调用。",
    )
    parser.add_argument(
        "--doc-id-base",
        type=int,
        default=90000,
        help="批次起始 doc_id（默认 90000，远离前端正常区间）",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    backend_root = repo_root / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    if args.dry_run:
        _patch_graph_repository_to_stub()
        print("(dry-run) GraphRepository 已 stub，不连真 Neo4j")

    from app.skills.workflow.passage_ingestion_workflow import (
        PassageIngestionWorkflowSkill,
    )

    output_dir = Path(args.output_dir) if args.output_dir else repo_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input_path).resolve()
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(input_path.rglob("*.txt"))

    if not files:
        print(f"未找到任何 .txt：{input_path}")
        return 1

    print(f"将跑 {len(files)} 篇 → {output_dir}")
    skill = PassageIngestionWorkflowSkill()

    failures = 0
    for idx, fp in enumerate(files, 1):
        doc_id = args.doc_id_base + idx
        try:
            context = _build_context(repo_root, fp, doc_id, output_dir)
            result = skill.run(context, arguments={})
            print(
                f"  [{idx:03d}] {fp.name:<55} status={result['status']:<8} "
                f"warnings={result['warning_count']:<3} → {Path(result['output_path']).name}"
            )
        except Exception as exc:
            failures += 1
            print(f"  [{idx:03d}] {fp.name} ERROR: {exc}")

    print()
    print(f"完成。失败 {failures} 篇 / 共 {len(files)} 篇")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
