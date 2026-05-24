from app.observability.schemas import LLMCost, LLMUsage


def estimate_cost(
    usage: LLMUsage,
    cache_hit_input_price_per_1m: float,
    cache_miss_input_price_per_1m: float,
    output_price_per_1m: float,
    currency: str,
) -> LLMCost:
    hit = usage.prompt_cache_hit_tokens
    miss = usage.prompt_cache_miss_tokens or 0
    input_cost = (
        hit * cache_hit_input_price_per_1m
        + miss * cache_miss_input_price_per_1m
    ) / 1_000_000
    output_cost = usage.completion_tokens * output_price_per_1m / 1_000_000
    return LLMCost(
        input_cost=round(input_cost, 10),
        output_cost=round(output_cost, 10),
        total_cost=round(input_cost + output_cost, 10),
        currency=currency,
    )
