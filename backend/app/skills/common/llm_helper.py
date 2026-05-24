"""LLM 调用 + 结构化输出校验 + 自动重试。

每个 atomic Skill 都通过本模块统一调 LLM；JSON 解析 / pydantic 校验 /
重试 / 兜底全部在这里收口，避免散落在各 Skill 内。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.providers.factory import get_llm_provider
from app.observability.llm_tracer import traced_chat_completion

T = TypeVar("T", bound=BaseModel)


class LLMStructuredError(RuntimeError):
    """LLM 多次重试仍未给出合法结构化输出。"""


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _truncate_prompt(text: str, max_chars: int = 6000) -> str:
    """对传入 LLM 的 user_prompt 做长度保护，超过 max_chars 时截断并附标记。"""
    if len(text) <= max_chars:
        return text
    original_len = len(text)
    return text[:max_chars] + f"[...截断，原始 {original_len} 字符]"


def _parse_json(content: str) -> Any:
    """容错解析 JSON：先去 markdown 代码块，再退化到截取首尾大括号 / 方括号。"""

    text = (content or "").strip()
    if not text:
        raise json.JSONDecodeError("empty content", "", 0)

    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("{", "}"), ("[", "]")):
            first = text.find(opener)
            last = text.rfind(closer)
            if 0 <= first < last:
                try:
                    return json.loads(text[first : last + 1])
                except json.JSONDecodeError:
                    continue
        raise


def call_llm_structured(
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    skill_code: str,
    max_retries: int = 2,
    additional_metadata: dict[str, Any] | None = None,
) -> T:
    """调用 LLM 期待 JSON 输出，按 pydantic schema 校验，失败自动重试。"""

    provider = get_llm_provider()
    last_diag: str | None = None
    metadata = {"skill_code": skill_code, **(additional_metadata or {})}

    # 传入 LLM 前对 user_prompt 做长度保护，避免大量工具结果撑爆 token 限制
    safe_user_prompt = _truncate_prompt(user_prompt)

    for attempt in range(max_retries + 1):
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": safe_user_prompt},
        ]
        if attempt > 0 and last_diag:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上次返回有问题：{last_diag}。"
                        "请严格按要求重新输出 JSON，不要包含 markdown 代码块或解释。"
                    ),
                }
            )

        try:
            trace_context = {
                **metadata,
                "skill_code": skill_code,
                "call_purpose": metadata.get("purpose") or "structured_output",
            }
            content = traced_chat_completion(
                provider=provider,
                messages=messages,
                metadata=metadata,
                trace_context=trace_context,
            )
            payload = _parse_json(content)
            return schema(**payload)
        except json.JSONDecodeError as exc:
            last_diag = f"JSON 解析失败：{exc.msg}"
        except ValidationError as exc:
            errs = exc.errors()[:2]
            last_diag = f"字段校验失败：{errs}"
        except Exception as exc:  # Provider/network errors are surfaced to workflow warnings.
            last_diag = f"LLM 调用失败：{type(exc).__name__}: {exc}"

    raise LLMStructuredError(
        f"{skill_code} LLM 重试 {max_retries + 1} 次仍失败：{last_diag}"
    )
