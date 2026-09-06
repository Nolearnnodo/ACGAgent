"""DeepSeek provider with usage parsing for remote prompt-cache metrics."""

import json
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm.base import BaseLLMProvider
from app.llm.providers.mock_provider import MockLLMProvider
from app.observability.schemas import LLMCallResult, LLMUsage

MAX_INPUT_TOKENS = 6000
_RETRY_DELAYS = (1, 2)


def _estimate_tokens(text: str) -> int:
    return len(text) // 2


def _messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for msg in messages:
        total += _estimate_tokens(msg.get("content", ""))
        total += _estimate_tokens(msg.get("role", ""))
    return total


def _trim_messages(
    messages: list[dict[str, str]],
    max_input_tokens: int,
) -> list[dict[str, str]]:
    if len(messages) <= 2:
        return messages
    if _messages_tokens(messages) <= max_input_tokens:
        return messages

    head = messages[0]
    tail = messages[-1]
    middle = list(messages[1:-1])
    while middle and _messages_tokens([head] + middle + [tail]) > max_input_tokens:
        middle.pop(0)
    return [head] + middle + [tail]


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return False


def _is_fatal_auth(exc: Exception) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {401, 403}
    )


def _parse_usage(raw_usage: dict[str, Any] | None) -> LLMUsage:
    raw = raw_usage or {}
    prompt_tokens = int(raw.get("prompt_tokens") or 0)
    hit_tokens = int(raw.get("prompt_cache_hit_tokens") or 0)
    miss_tokens = raw.get("prompt_cache_miss_tokens")
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=int(raw.get("completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        prompt_cache_hit_tokens=hit_tokens,
        prompt_cache_miss_tokens=(
            int(miss_tokens)
            if miss_tokens is not None
            else max(prompt_tokens - hit_tokens, 0)
        ),
        cache_metrics_supported=(
            "prompt_cache_hit_tokens" in raw
            or "prompt_cache_miss_tokens" in raw
        ),
    )


class DeepSeekProvider(BaseLLMProvider):
    """Provider backed by the DeepSeek chat completions API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base_url: str | None = None,
        model_name: str | None = None,
        temperature: float | None = None,
        include_temperature: bool = True,
        timeout_seconds: int | None = None,
        disable_timeout: bool = False,
    ) -> None:
        self.settings = get_settings()
        self.mock_provider = MockLLMProvider()
        # None 表示沿用 settings 的动态值；保留这一点可以兼容测试和运行时配置刷新。
        self.api_key = api_key
        self.api_base_url = api_base_url
        self.model_name = model_name
        self.temperature = (
            0.1 if temperature is None else temperature
        )
        self.include_temperature = include_temperature
        self.disable_timeout = disable_timeout
        self.timeout_seconds = (
            None
            if disable_timeout
            else (
                self.settings.llm_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            )
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._effective_api_key}",
            "Content-Type": "application/json",
        }

    @property
    def _effective_api_key(self) -> str:
        return self.settings.llm_api_key if self.api_key is None else self.api_key

    @property
    def _effective_api_base_url(self) -> str:
        return self.settings.llm_api_base_url if self.api_base_url is None else self.api_base_url

    @property
    def _effective_model_name(self) -> str:
        return self.settings.llm_model_name if self.model_name is None else self.model_name

    @property
    def _effective_timeout_seconds(self) -> int | None:
        if self.disable_timeout:
            return None
        return self.settings.llm_timeout_seconds if self.timeout_seconds is None else self.timeout_seconds

    def _post_chat_completion_result(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> LLMCallResult:
        api_base_url = self._effective_api_base_url
        api_key = self._effective_api_key
        model_name = self._effective_model_name
        if not api_base_url or not api_key or not model_name:
            raise ValueError("DeepSeek API configuration is incomplete.")

        trimmed = _trim_messages(messages, MAX_INPUT_TOKENS)
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": trimmed,
        }
        if self.include_temperature:
            payload["temperature"] = self.temperature
        if (metadata or {}).get("response_format") != "text":
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        response_body = bytearray()
        client_timeout = (
            None
            if self.disable_timeout
            else httpx.Timeout(max(float(self._effective_timeout_seconds or 0), 1.0))
        )
        # 以 stream 读取响应，兼容响应体较大的模型输出；标注调用的 client_timeout 为 None。
        with httpx.Client(timeout=client_timeout) as client:
            with client.stream(
                "POST",
                f"{api_base_url.rstrip('/')}/chat/completions",
                headers=self._build_headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    response_body.extend(chunk)
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(bytes(response_body))
        return LLMCallResult(
            content=data["choices"][0]["message"]["content"],
            usage=_parse_usage(data.get("usage")),
            provider="deepseek",
            model=str(data.get("model") or model_name),
            raw_response=data,
            latency_ms=latency_ms,
        )

    def _post_chat_completion(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self._post_chat_completion_result(messages, metadata=metadata).content

    def _post_chat_completion_result_with_retry(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> LLMCallResult:
        last_exc: Exception | None = None
        started = time.perf_counter()
        retry_enabled = (metadata or {}).get("retry", True) is not False
        retry_delays = [0] + list(_RETRY_DELAYS) if retry_enabled else [0]
        for attempt, delay in enumerate(retry_delays):
            if delay:
                time.sleep(delay)
            try:
                result = self._post_chat_completion_result(messages, metadata=metadata)
                result.retry_count = attempt
                result.latency_ms = int((time.perf_counter() - started) * 1000)
                return result
            except Exception as exc:
                if _is_fatal_auth(exc):
                    raise
                if _is_retryable(exc):
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _post_chat_completion_with_retry(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self._post_chat_completion_result_with_retry(
            messages,
            metadata=metadata,
        ).content

    def generate_structured_intent(self, prompt: str, metadata: dict[str, Any]) -> dict[str, Any]:
        allowed_skill_codes = [
            "conversation_reply_workflow",
            "graph_query_atomic",
            "graph_write_atomic",
            "query_workflow",
        ]
        system_prompt = (
            "你是一个受控 Planner，只能从给定 Skill 中选择一个执行目标，"
            "不能自由生成多步计划。"
            "请严格返回 JSON 对象，包含字段："
            "intent, decision_type, target_skill_code, reason, arguments。"
            "decision_type 只能是 skill、workflow、reject。"
            f"可选 target_skill_code 仅允许：{', '.join(allowed_skill_codes)}。"
            "如果用户请求涉及图写入且用户不是 admin，则返回 reject。"
            "如果用户请求是查询类，请路由到 query_workflow。"
        )
        user_prompt = {
            "user_prompt": prompt,
            "user_role": metadata.get("user_role", "user"),
            "recent_messages": metadata.get("recent_messages", []),
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ]

        try:
            content = self._post_chat_completion_with_retry(messages)
        except RuntimeError:
            raise
        except httpx.HTTPStatusError as exc:
            if _is_fatal_auth(exc):
                raise RuntimeError(
                    f"DeepSeek API authentication failed ({exc.response.status_code}); "
                    "please check API key configuration."
                ) from exc
            return self.mock_provider.generate_structured_intent(prompt, metadata)
        except (httpx.TimeoutException, httpx.RequestError):
            return self.mock_provider.generate_structured_intent(prompt, metadata)
        except Exception:
            return self.mock_provider.generate_structured_intent(prompt, metadata)

        try:
            result = json.loads(content)
            required_keys = {"intent", "decision_type", "target_skill_code", "reason", "arguments"}
            if not required_keys.issubset(result):
                raise ValueError("DeepSeek response missing required planner fields.")
            if result["decision_type"] not in {"skill", "workflow", "reject"}:
                raise ValueError("DeepSeek returned invalid decision_type.")
            if result["target_skill_code"] not in allowed_skill_codes and result["decision_type"] != "reject":
                raise ValueError("DeepSeek returned an unregistered skill code.")
            if result["decision_type"] == "reject":
                result["target_skill_code"] = "permission_denied"
            result["arguments"] = result.get("arguments") or {"user_prompt": prompt}
            if "user_prompt" not in result["arguments"]:
                result["arguments"]["user_prompt"] = prompt
            return result
        except (json.JSONDecodeError, ValueError, KeyError):
            return self.mock_provider.generate_structured_intent(prompt, metadata)

    def chat_completion(self, messages: list[dict[str, str]], metadata: dict[str, Any]) -> str:
        try:
            return self._post_chat_completion_with_retry(messages, metadata=metadata)
        except httpx.HTTPStatusError as exc:
            if _is_fatal_auth(exc):
                raise RuntimeError(
                    f"DeepSeek API authentication failed ({exc.response.status_code}); "
                    "please check API key configuration."
                ) from exc
            raise

    def chat_completion_with_usage(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any],
    ) -> LLMCallResult:
        try:
            return self._post_chat_completion_result_with_retry(
                messages,
                metadata=metadata,
            )
        except httpx.HTTPStatusError as exc:
            if _is_fatal_auth(exc):
                raise RuntimeError(
                    f"DeepSeek API authentication failed ({exc.response.status_code}); "
                    "please check API key configuration."
                ) from exc
            raise
