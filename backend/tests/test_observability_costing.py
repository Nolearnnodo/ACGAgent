from app.observability.costing import estimate_cost
from app.observability.schemas import LLMUsage


def test_estimate_cost_uses_remote_cache_hit_and_miss_prices():
    usage = LLMUsage(
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        prompt_cache_hit_tokens=700,
        prompt_cache_miss_tokens=300,
        cache_metrics_supported=True,
    )

    cost = estimate_cost(
        usage=usage,
        cache_hit_input_price_per_1m=0.1,
        cache_miss_input_price_per_1m=1.0,
        output_price_per_1m=2.0,
        currency="CNY",
    )

    assert cost.input_cost == 0.00037
    assert cost.output_cost == 0.0004
    assert cost.total_cost == 0.00077
    assert cost.currency == "CNY"


def test_usage_without_cache_metrics_treats_all_prompt_tokens_as_miss():
    usage = LLMUsage(prompt_tokens=100, completion_tokens=10, total_tokens=110)

    assert usage.prompt_cache_hit_tokens == 0
    assert usage.prompt_cache_miss_tokens == 100
    assert usage.cache_hit_ratio == 0
