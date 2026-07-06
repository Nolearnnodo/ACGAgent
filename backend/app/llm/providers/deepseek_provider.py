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
    max_tokens: int,
) -> list[dict[str, str]]:
    if len(messages) <= 2:
        return messages
    if _messages_tokens(messages) <= max_tokens:
        return messages

    head = messages[0]
    tail = messages[-1]
    middle = list(messages[1:-1])
    while middle and _messages_tokens([head] + middle + [tail]) > max_tokens:
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

    def __init__(self) -> None:
        self.settings = get_settings()
        self.mock_provider = MockLLMProvider()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat_completion_result(
        self,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
    ) -> LLMCallResult:
        if not self.settings.llm_api_base_url or not self.settings.llm_api_key:
            raise ValueError("DeepSeek API configuration is incomplete.")

        trimmed = _trim_messages(messages, MAX_INPUT_TOKENS)
        payload: dict[str, Any] = {
            "model": self.settings.llm_model_name,
            "messages": trimmed,
            "temperature": 0.1,
        }
        if (metadata or {}).get("response_format") != "text":
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        response = httpx.post(
            f"{self.settings.llm_api_base_url.rstrip('/')}/chat/completions",
            headers=self._build_headers(),
            json=payload,
            timeout=self.settings.llm_timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        return LLMCallResult(
            content=data["choices"][0]["message"]["content"],
            usage=_parse_usage(data.get("usage")),
            provider="deepseek",
            model=str(data.get("model") or self.settings.llm_model_name),
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
        for attempt, delay in enumerate([0] + list(_RETRY_DELAYS)):
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
