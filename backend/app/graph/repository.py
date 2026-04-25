"""图数据库统一访问层。

当前仓储层负责统一执行 Neo4j 查询，并把结果整理成稳定的返回结构。
"""

from typing import Any

from app.graph.client import Neo4jClient


class GraphRepository:
    """图数据库仓储层。"""

    def __init__(self, client: Neo4jClient | None = None) -> None:
        self.client = client or Neo4jClient()

    def _extract_query(self, cypher: str, parameters: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """从 parameters 中提取真正要执行的 Cypher 与绑定参数。

        为了兼容后续 Skill 的统一调用约定，这里优先读取 `parameters["cypher"]`
        或 `parameters["query"]`，如果没有再回退到方法参数中的 `cypher`。
        """

        query_parameters = dict(parameters or {})
        actual_cypher = query_parameters.pop("cypher", None) or query_parameters.pop("query", None) or cypher

        if not actual_cypher or not str(actual_cypher).strip():
            raise ValueError("未提供可执行的 Neo4j Cypher 语句。")

        return str(actual_cypher).strip(), query_parameters

    def _build_summary(self, summary) -> dict[str, Any]:
        """提取查询摘要，便于前端或上层流程查看写入效果。"""

        counters = summary.counters
        return {
            "query_type": summary.query_type,
            "database": summary.database,
            "contains_updates": counters.contains_updates,
            "nodes_created": counters.nodes_created,
            "nodes_deleted": counters.nodes_deleted,
            "relationships_created": counters.relationships_created,
            "relationships_deleted": counters.relationships_deleted,
            "properties_set": counters.properties_set,
            "labels_added": counters.labels_added,
            "labels_removed": counters.labels_removed,
        }

    def run_read_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行只读查询并返回结果数据。"""

        actual_cypher, query_parameters = self._extract_query(cypher, parameters)
        driver = self.client.get_driver()

        with driver.session(database=self.client.settings.neo4j_database) as session:
            result = session.run(actual_cypher, query_parameters)
            records = [record.data() for record in result]
            summary = result.consume()

        return {
            "status": "success",
            "operation": "read",
            "cypher": actual_cypher,
            "parameters": query_parameters,
            "record_count": len(records),
            "records": records,
            "summary": self._build_summary(summary),
        }

    def run_write_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行写入查询并返回执行结果。"""

        actual_cypher, query_parameters = self._extract_query(cypher, parameters)
        driver = self.client.get_driver()

        with driver.session(database=self.client.settings.neo4j_database) as session:
            result = session.run(actual_cypher, query_parameters)
            records = [record.data() for record in result]
            summary = result.consume()

        return {
            "status": "success",
            "operation": "write",
            "cypher": actual_cypher,
            "parameters": query_parameters,
            "record_count": len(records),
            "records": records,
            "summary": self._build_summary(summary),
        }
