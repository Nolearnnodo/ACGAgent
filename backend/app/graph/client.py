"""Neo4j 客户端封装。

`neo4j` 第三方包仅在真正建立连接时才 import，
这样在未安装 neo4j 库或 driver 不可用的情况下，
依赖 GraphRepository 的模块依然可以被加载（用于单元测试 / 离线场景）。
"""

from app.core.config import get_settings


class Neo4jClient:
    """负责管理 Neo4j Driver 生命周期。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._driver = None

    def connect(self) -> None:
        """建立到 Neo4j 的连接。"""

        if self._driver is None:
            from neo4j import GraphDatabase  # 延迟导入

            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password),
            )

    def get_driver(self):
        """获取底层 driver。"""

        if self._driver is None:
            self.connect()
        return self._driver

    def close(self) -> None:
        """关闭连接。"""

        if self._driver is not None:
            self._driver.close()
            self._driver = None
