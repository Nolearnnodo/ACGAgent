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


def test_hiagent_annotation_uses_apikey_and_keeps_formal_answer(monkeypatch):
    captured = {}

    class _Created:
        def raise_for_status(self):
            return None

        def json(self):
            return {"Conversation": {"AppConversationID": "conv-1"}}

    class _Chat:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return [
                "event:text",
                '{"event":"message_start","app_version":"a1.04"}',
                'data: {"event":"think_message","answer":"忽略"}',
                'data: {"event":"message","answer":"{\\"ok\\":"}',
                'data: {"event":"message","answer":"true}"}',
                'data: {"event":"message_cost","input_tokens":11,"output_tokens":4,"app_version":"a1.04"}',
                'data: {"event":"message_end"}',
            ]

    class _Stream:
        def __enter__(self):
            return _Chat()

        def __exit__(self, *_args):
            return False

    class _Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, headers, json):
            captured["create_url"] = url
            captured["headers"] = headers
            captured["create_body"] = json
            return _Created()

        def stream(self, _method, url, *, headers, json):
            captured["chat_url"] = url
            captured["chat_body"] = json
            return _Stream()

    monkeypatch.setattr("app.llm.providers.deepseek_provider.httpx.Client", _Client)
    provider = DeepSeekProvider(
        api_key="secret-key",
        api_base_url="https://agent.tongji.edu.cn/api/proxy/api/v1",
        model_name="glm-5.3-flash",
        include_temperature=False,
        disable_timeout=True,
    )

    result = provider.chat_completion_with_usage(
        messages=[
            {"role": "system", "content": "只输出 JSON"},
            {"role": "user", "content": '{"task_id": 25}'},
        ],
        metadata={"response_format": "json", "retry": False},
    )

    assert result.content == '{"ok":true}'
    assert result.model == "glm-5.3-flash"
    assert result.provider == "hiagent"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 4
    assert result.raw_response["choices"][0]["finish_reason"] == "stop"
    assert captured["headers"]["Apikey"] == "secret-key"
    assert "Authorization" not in captured["headers"]
    assert captured["create_url"].endswith("/create_conversation")
    assert captured["chat_url"].endswith("/chat_query_v2")
    assert captured["chat_body"]["ResponseMode"] == "streaming"
    assert captured["chat_body"]["Query"] == "只输出 JSON\n\n用户消息 JSON：\n{\"task_id\": 25}"
    assert "response_format" not in captured["chat_body"]
    assert "secret-key" not in str(result.raw_response)


def test_hiagent_message_failed_raises_runtime_error(monkeypatch):
    class _Created:
        def raise_for_status(self):
            return None

        def json(self):
            return {"Conversation": {"AppConversationID": "conv-1"}}

    class _Chat:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return ['data: {"event":"message_failed","error":"大模型接口调用出错"}']

    class _Stream:
        def __enter__(self):
            return _Chat()

        def __exit__(self, *_args):
            return False

    class _Client:
        def __init__(self, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url, *, headers, json):
            return _Created()

        def stream(self, _method, _url, *, headers, json):
            return _Stream()

    monkeypatch.setattr("app.llm.providers.deepseek_provider.httpx.Client", _Client)
    provider = DeepSeekProvider(
        api_key="secret-key",
        api_base_url="https://agent.tongji.edu.cn/api/proxy/api/v1",
        model_name="glm-5.3-flash",
        disable_timeout=True,
    )

    try:
        provider.chat_completion_with_usage(
            messages=[{"role": "user", "content": "标注"}],
            metadata={"retry": False},
        )
    except RuntimeError as exc:
        assert "大模型接口调用出错" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
