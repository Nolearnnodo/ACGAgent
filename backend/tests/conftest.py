"""测试公共初始化。

- 把 backend 目录加入导入路径，便于直接从仓库根目录运行 pytest。
- 自动 stub `GraphRepository`，避免测试真连 Neo4j。
- 自动 reset `dictionary_service` 缓存，避免跨测试污染。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _force_mock_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stub_graph_repository(monkeypatch):
    """把 GraphRepository 类替换成不联网的 stub，所有 upsert_xxx 返回成功占位。"""

    from app.graph import repository as repo_module

    def make_stub() -> Any:
        # 不带 spec —— MagicMock 自动孵化任何被访问的方法。
        stub = MagicMock()
        success = {"status": "success", "operation": "stub"}
        for method in [
            "upsert_passage_info",
            "upsert_person",
            "upsert_life_event",
            "upsert_historical_event_time_link",
            "upsert_person_historical_event",
            "upsert_person_relation",
            "merge_same_name_persons_for_passage",
            "merge_person_nodes",
            "run_read_query",
            "run_write_query",
        ]:
            getattr(stub, method).return_value = success
        stub.merge_same_name_persons_for_passage.return_value = {
            "status": "success",
            "doc_id": 0,
            "matched_person_count": 0,
            "merged_person_count": 0,
            "failed_merge_count": 0,
            "merges": [],
            "failures": [],
        }
        return stub

    monkeypatch.setattr(repo_module, "GraphRepository", make_stub)

    # Skill 内部用 `from app.graph.repository import GraphRepository` 已经绑过来；
    # 这些模块持有的引用也要替换。
    for skill_module in (
        "app.skills.atomic.passage_meta",
        "app.skills.atomic.person_layer",
        "app.skills.atomic.event_relation",
        "app.skills.atomic.person_exact_match_merge",
    ):
        monkeypatch.setattr(f"{skill_module}.GraphRepository", make_stub)


@pytest.fixture(autouse=True)
def _stub_passage_meta_session(monkeypatch):
    """Avoid requiring a migrated SQLite database in workflow unit tests."""

    class _NoopSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, key):
            return None

        def add(self, item):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(
        "app.skills.atomic.passage_meta.SessionLocal",
        lambda: _NoopSession(),
    )


@pytest.fixture(autouse=True)
def _reset_dict_cache():
    """每条用例前清理字典 service 的进程级缓存。"""

    from app.services import dictionary_service as ds

    ds.reset_cache()
    ds._cache["era_entries"] = [
        ds.EraEntry(
            era="开元",
            dynasty="唐",
            start_year=713,
            end_year=741,
            simplified="开元",
            traditional="開元",
        )
    ]
    ds._cache["historical_events"] = []
    ds._cache["source_types"] = {0: "墓志铭/塔铭", 1: "一般历史文献"}
    ds._cache["relation_codes"] = {
        code: ds.RelationCodeEntry(code=code, meaning=code, direction_hint="")
        for code in ("F", "M", "S", "D", "H", "W", "Z", "C", "B", "O")
    }
    yield
    ds.reset_cache()
