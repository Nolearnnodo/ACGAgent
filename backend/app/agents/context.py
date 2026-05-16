"""统一执行上下文。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Executor 在整个执行过程共享的上下文对象。"""

    user: dict[str, Any]
    conversation: dict[str, Any]
    variables: dict[str, Any] = field(default_factory=dict)
    step_results: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_step_result(self, key: str, value: Any) -> None:
        """记录步骤结果，供后续 step 引用。"""

        self.step_results[key] = value

    def resolve_value(self, value: Any) -> Any:
        """解析类似 `$step1_result.xxx` 的上下文引用。

        当前仅实现最基础的变量直取，后续可继续增强为更复杂的模板解析。
        """

        if isinstance(value, str) and value.startswith("$"):
            key = value[1:]
            return self.step_results.get(key) or self.variables.get(key)
        return value
