"""图数据库统一访问层。

当前先提供稳定接口与占位实现，后续 Atomic Skill 将调用这里。
"""

from typing import Any

from app.graph.client import Neo4jClient


class GraphRepository:
    """图数据库仓储层。"""

    def __init__(self, client: Neo4jClient | None = None) -> None:
        self.client = client or Neo4jClient()

    def run_read_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行只读查询。

        目前暂不真正访问 Neo4j，仅返回占位结构，方便上层先完成接口联调。
        """

        return {
            "status": "placeholder",
            "operation": "read",
            "cypher": cypher,
            "parameters": parameters or {},
            "records": [],
        }

    def run_write_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行写入查询。

        真正的图写入逻辑后续会下沉为受控 Skill，这里先保留接口。
        """

        return {
            "status": "placeholder",
            "operation": "write",
            "cypher": cypher,
            "parameters": parameters or {},
        }
