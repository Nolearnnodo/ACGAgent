from app.llm.providers.deepseek_provider import _parse_usage


def test_parse_deepseek_usage_includes_remote_cache_fields():
    usage = _parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_cache_hit_tokens": 700,
            "prompt_cache_miss_tokens": 300,
        }
    )

    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 200
    assert usage.total_tokens == 1200
    assert usage.prompt_cache_hit_tokens == 700
    assert usage.prompt_cache_miss_tokens == 300
    assert usage.cache_metrics_supported is True
    assert usage.cache_hit_ratio == 0.7


def test_parse_deepseek_usage_without_cache_fields_marks_all_prompt_tokens_as_miss():
    usage = _parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        }
    )

    assert usage.prompt_cache_hit_tokens == 0
    assert usage.prompt_cache_miss_tokens == 1000
    assert usage.cache_metrics_supported is False
    assert usage.cache_hit_ratio == 0
