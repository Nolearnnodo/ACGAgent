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
            "run_read_query",
            "run_write_query",
        ]:
            getattr(stub, method).return_value = success
        return stub

    monkeypatch.setattr(repo_module, "GraphRepository", make_stub)

    # Skill 内部用 `from app.graph.repository import GraphRepository` 已经绑过来；
    # 这些模块持有的引用也要替换。
    for skill_module in (
        "app.skills.atomic.passage_meta",
        "app.skills.atomic.person_layer",
        "app.skills.atomic.event_relation",
    ):
        monkeypatch.setattr(f"{skill_module}.GraphRepository", make_stub)


@pytest.fixture(autouse=True)
def _reset_dict_cache():
    """每条用例前清理字典 service 的进程级缓存。"""

    from app.services.dictionary_service import reset_cache

    reset_cache()
    yield
    reset_cache()
