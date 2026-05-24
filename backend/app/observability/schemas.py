from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int | None = None
    cache_metrics_supported: bool = False

    def __post_init__(self) -> None:
        self.prompt_tokens = max(int(self.prompt_tokens or 0), 0)
        self.completion_tokens = max(int(self.completion_tokens or 0), 0)
        self.prompt_cache_hit_tokens = max(int(self.prompt_cache_hit_tokens or 0), 0)
        if self.prompt_cache_miss_tokens is None:
            self.prompt_cache_miss_tokens = max(
                self.prompt_tokens - self.prompt_cache_hit_tokens,
                0,
            )
        else:
            self.prompt_cache_miss_tokens = max(int(self.prompt_cache_miss_tokens or 0), 0)
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        self.total_tokens = max(int(self.total_tokens or 0), 0)

    @property
    def cache_hit_ratio(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return self.prompt_cache_hit_tokens / self.prompt_tokens


@dataclass
class LLMCost:
    input_cost: float
    output_cost: float
    total_cost: float
    currency: str


@dataclass
class LLMCallResult:
    content: str
    usage: LLMUsage
    provider: str
    model: str
    raw_response: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    retry_count: int = 0
