"""Skill 基础抽象。"""

from abc import ABC, abstractmethod
from typing import Any

from app.agents.context import ExecutionContext


class BaseSkill(ABC):
    """所有 Atomic / Workflow Skill 的统一接口。"""

    code: str = ""
    allowed_roles: list[str] = ["user"]

    @abstractmethod
    def run(self, context: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 Skill。"""
