"""Neo4j 客户端封装。"""

from neo4j import GraphDatabase

from app.core.config import get_settings


class Neo4jClient:
    """负责管理 Neo4j Driver 生命周期。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._driver = None

    def connect(self) -> None:
        """建立到 Neo4j 的连接。"""

        if self._driver is None:
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
