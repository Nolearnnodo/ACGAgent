"""Stage 4：把上游 Skill 产物渲染为 YAML 写入 output/ 目录。

不调 LLM。直接读 ExecutionContext.metadata 中累积的 4 段产物：
- probe / passage_meta / person_layer / event_relation
按 output/README.md 规范拼装为 YAML 文件。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.agents.context import ExecutionContext
from app.core.config import get_settings
from app.skills.base import BaseSkill

# 默认 output 目录：仓库根 output/。如果工作目录不同也能定位到仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output"


def _safe_filename(s: str, limit: int = 30) -> str:
    """把任意字符串转为安全文件名（Windows 与 Unix 通用）。"""

    bad = '<>:"/\\|?*\n\r\t'
    s = "".join("_" if c in bad else c for c in s).strip()
    s = s.replace(" ", "_").replace("《", "").replace("》", "")
    return s[:limit] or "untitled"


class _LiteralStr(str):
    """让 PyYAML 把长字符串渲染为 | 块状形式，可读性更好。"""


def _literal_presenter(dumper, data):
    if "\n" in data or len(data) > 60:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(_LiteralStr, _literal_presenter)


def _wrap_long(value: Any) -> Any:
    """递归把超过 60 字符的字符串包成 _LiteralStr 渲染。"""

    if isinstance(value, str):
        return _LiteralStr(value) if len(value) > 60 or "\n" in value else value
    if isinstance(value, list):
        return [_wrap_long(v) for v in value]
    if isinstance(value, dict):
        return {k: _wrap_long(v) for k, v in value.items()}
    return value


class PassageFormatOutputAtomicSkill(BaseSkill):
    """Stage 4：渲染 YAML 输出文件。"""

    code = "passage_format_output_atomic"
    allowed_roles = ["user", "admin"]

    def run(self, context: ExecutionContext, arguments: dict) -> dict:
        passage = context.metadata.get("passage", {})
        doc_id = int(passage["doc_id"])
        title = passage.get("title", "untitled")

        probe = context.metadata.get("probe", {})
        passage_meta = context.metadata.get("passage_meta", {})
        person_layer = context.metadata.get("person_layer", {"persons": [], "pair_relations": []})
        event_relation = context.metadata.get("event_relation", {"person_events": [], "person_relations": []})
        warnings = context.metadata.get("warnings", [])

        # 整理 persons 视图：把 event_relation 的 life_events / historic_events 按 person_id 内联进 persons
        events_by_person: dict[int, dict[str, Any]] = {}
        for pe in event_relation.get("person_events", []):
            events_by_person[int(pe["person_id"])] = pe

        persons_view: list[dict[str, Any]] = []
        for p in person_layer.get("persons", []):
            pid = int(p["person_id"])
            view = {
                "person_id": pid,
                "level": int(p["level"]),
                "name": p["name"],
                "zi": p.get("zi"),
                "titles": p.get("titles") or [],
                "life_events": (events_by_person.get(pid) or {}).get("life_events") or [],
                "historic_events": (events_by_person.get(pid) or {}).get("historic_events") or [],
            }
            persons_view.append(view)

        # 统计
        level_counts = {1: 0, 2: 0, 3: 0}
        life_events_count = 0
        unique_eras: set[str] = set()
        unique_event_names: set[str] = set()
        for v in persons_view:
            level_counts[v["level"]] = level_counts.get(v["level"], 0) + 1
            if v["level"] in (1, 2):
                life_events_count += len(v["life_events"])
                for ev in v["life_events"]:
                    if ev.get("time") and ev["time"].get("era"):
                        unique_eras.add(ev["time"]["era"])
                for he in v["historic_events"]:
                    unique_event_names.add(he["event_name"])

        source_type_code = passage_meta.get("source_type_code")
        source = "epitaph" if source_type_code == 0 else "history"

        document: dict[str, Any] = {
            "meta": {
                "doc_id": doc_id,
                "source_file": passage.get("source_file") or "",
                "source_type": source_type_code,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "extractor_version": "1.0.0",
                "llm_model": get_settings().llm_model_name,
                "id_base": 1000,
                "probe": probe,
            },
            "passage_info": {
                "doc_id": doc_id,
                "title": title,
                "source_type": source_type_code,
                "era": passage_meta.get("era"),
                "dynasty": passage_meta.get("dynasty"),
            },
            "persons": persons_view,
            "person_relations": event_relation.get("person_relations", []),
            "stats": {
                "level_1_count": level_counts.get(1, 0),
                "level_2_count": level_counts.get(2, 0),
                "level_3_count": level_counts.get(3, 0),
                "life_events_count": life_events_count,
                "unique_eras": len(unique_eras),
                "unique_historic_events": len(unique_event_names),
                "person_relations_count": len(event_relation.get("person_relations", [])),
            },
            "warnings": warnings,
        }

        document = _wrap_long(document)

        # 决定输出目录：metadata 可指定，否则用仓库根 output/
        output_dir = Path(context.metadata.get("output_dir") or _DEFAULT_OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{source}_{doc_id:04d}_{_safe_filename(title)}.txt"
        output_path = output_dir / filename

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 文章 #{doc_id} 抽取结果（由 functionA workflow 自动生成）\n")
            yaml.dump(
                document,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=100,
            )

        return {
            "output_path": str(output_path),
            "stats": document["stats"],
            "warning_count": len(warnings),
        }
