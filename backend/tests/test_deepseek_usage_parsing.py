from app.llm.providers.deepseek_provider import _parse_usage
from app.llm.providers.deepseek_provider import DeepSeekProvider


def _patch_stream_client(monkeypatch, captured, response_body):
    class _Response:
        def raise_for_status(self):
            return None

        def iter_bytes(self):
            return [response_body]

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *_args):
            return False

    class _Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method, _url, *, headers, json):
            captured["json"] = json
            return _Stream()

    monkeypatch.setattr("app.llm.providers.deepseek_provider.httpx.Client", _Client)


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


def test_deepseek_text_response_format_omits_json_response_format(monkeypatch):
    captured = {}
    _patch_stream_client(
        monkeypatch,
        captured,
        b'{"choices":[{"message":{"content":"markdown report"}}],"usage":{},"model":"deepseek-test"}',
    )
    provider = DeepSeekProvider()
    provider.settings.llm_api_base_url = "https://example.test"
    provider.settings.llm_api_key = "key"
    provider.settings.llm_model_name = "deepseek-test"

    result = provider.chat_completion(
        messages=[{"role": "user", "content": "生成报告"}],
        metadata={"response_format": "text"},
    )

    assert result == "markdown report"
    assert "response_format" not in captured["json"]


def test_deepseek_annotation_request_can_omit_temperature_and_max_tokens(monkeypatch):
    captured = {}
    _patch_stream_client(
        monkeypatch,
        captured,
        b'{"choices":[{"message":{"content":"{}"}}],"usage":{},"model":"deepseek-test"}',
    )
    provider = DeepSeekProvider(
        api_key="key",
        api_base_url="https://example.test",
        model_name="deepseek-test",
        temperature=None,
        include_temperature=False,
        disable_timeout=True,
    )

    provider.chat_completion(
        messages=[{"role": "user", "content": "生成标注"}],
        metadata={"response_format": "json"},
    )

    assert "temperature" not in captured["json"]
    assert "max_tokens" not in captured["json"]
    assert captured["timeout"] is None
