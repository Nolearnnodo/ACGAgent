"""Helpers for persisting step outputs without breaking JSON consumers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

_PREVIEW_KEY = "_step_output_preview"
_PREVIEW_PROFILES: tuple[tuple[int, int, int], ...] = (
    (20, 400, 6),
    (10, 240, 5),
    (5, 160, 4),
    (3, 96, 3),
    (1, 64, 2),
)
_FALLBACK_KEY_LIMIT = 30


def serialize_step_output(output: Any, max_chars: int = 8000) -> str:
    """Serialize step output into a bounded, always-valid JSON preview."""

    return serialize_json_preview(output, max_chars=max_chars)


def serialize_json_preview(
    payload: Any,
    max_chars: int = 8000,
    *,
    default: Callable[[Any], Any] | None = None,
) -> str:
    """Serialize payload into a bounded, always-valid JSON preview."""

    normalized = payload if payload is not None else {}
    serialized = _json_dumps(normalized, default=default)
    if len(serialized) <= max_chars:
        return serialized

    for list_limit, string_limit, depth_limit in _PREVIEW_PROFILES:
        metadata: dict[str, Any] = {}
        preview = _build_preview_value(
            normalized,
            metadata=metadata,
            path="$",
            list_limit=list_limit,
            string_limit=string_limit,
            depth_limit=depth_limit,
        )
        preview = _attach_preview_metadata(
            preview=preview,
            metadata=metadata,
            original_chars=len(serialized),
            max_chars=max_chars,
        )
        preview_serialized = json.dumps(preview, ensure_ascii=False)
        if len(preview_serialized) <= max_chars:
            return preview_serialized

    fallback = _build_fallback_preview(
        normalized,
        original_chars=len(serialized),
        max_chars=max_chars,
    )
    fallback_serialized = json.dumps(fallback, ensure_ascii=False)
    if len(fallback_serialized) <= max_chars:
        return fallback_serialized
    return json.dumps(
        {
            "value": _summarize_value(normalized),
            _PREVIEW_KEY: {
                "truncated": True,
                "original_chars": len(serialized),
                "max_chars": max_chars,
                "fallback": True,
                "value_omitted": True,
            },
        },
        ensure_ascii=False,
    )


def _json_dumps(value: Any, *, default: Callable[[Any], Any] | None) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=default)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _build_preview_value(
    value: Any,
    *,
    metadata: dict[str, Any],
    path: str,
    list_limit: int,
    string_limit: int,
    depth_limit: int,
) -> Any:
    if depth_limit <= 0:
        metadata[path] = {
            "kind": type(value).__name__,
            "reason": "depth_limit",
        }
        return _summarize_value(value)

    if isinstance(value, dict):
        return {
            str(key): _build_preview_value(
                item,
                metadata=metadata,
                path=f"{path}.{str(key)}",
                list_limit=list_limit,
                string_limit=string_limit,
                depth_limit=depth_limit - 1,
            )
            for key, item in value.items()
        }

    if isinstance(value, list):
        preview_items = [
            _build_preview_value(
                item,
                metadata=metadata,
                path=f"{path}[{index}]",
                list_limit=list_limit,
                string_limit=string_limit,
                depth_limit=depth_limit - 1,
            )
            for index, item in enumerate(value[:list_limit])
        ]
        if len(value) > list_limit:
            metadata[path] = {
                "kind": "list",
                "original_count": len(value),
                "returned_count": len(preview_items),
            }
        return preview_items

    if isinstance(value, str) and len(value) > string_limit:
        metadata[path] = {
            "kind": "string",
            "original_length": len(value),
            "returned_length": string_limit,
        }
        return value[:string_limit]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    rendered = repr(value)
    if len(rendered) > string_limit:
        metadata[path] = {
            "kind": type(value).__name__,
            "original_length": len(rendered),
            "returned_length": string_limit,
        }
        return rendered[:string_limit]
    return rendered


def _attach_preview_metadata(
    *,
    preview: Any,
    metadata: dict[str, Any],
    original_chars: int,
    max_chars: int,
) -> Any:
    meta = {
        "truncated": True,
        "original_chars": original_chars,
        "max_chars": max_chars,
    }
    if metadata:
        meta["fields"] = metadata

    if isinstance(preview, dict):
        result = dict(preview)
        result[_PREVIEW_KEY] = meta
        return result

    return {
        "value": preview,
        _PREVIEW_KEY: meta,
    }


def _build_fallback_preview(output: Any, *, original_chars: int, max_chars: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    if isinstance(output, dict):
        preview: dict[str, Any] = {}
        items = list(output.items())
        for key, value in items[:_FALLBACK_KEY_LIMIT]:
            preview_key = str(key)
            if len(preview_key) > 64:
                preview_key = preview_key[:64]
            path = f"$.{preview_key}"
            if isinstance(value, (int, float, bool)) or value is None:
                preview[preview_key] = value
                continue
            if isinstance(value, str):
                preview[preview_key] = value[:64]
                if len(value) > 64:
                    metadata[path] = {
                        "kind": "string",
                        "original_length": len(value),
                        "returned_length": 64,
                    }
                continue
            if isinstance(value, list):
                preview[preview_key] = []
                metadata[path] = {
                    "kind": "list",
                    "original_count": len(value),
                    "returned_count": 0,
                }
                continue
            if isinstance(value, dict):
                preview[preview_key] = {}
                metadata[path] = {
                    "kind": "dict",
                    "original_count": len(value),
                    "returned_count": 0,
                }
                continue
            preview[preview_key] = repr(value)[:64]
            metadata[path] = {
                "kind": type(value).__name__,
                "reason": "fallback_repr",
            }

        if len(items) > _FALLBACK_KEY_LIMIT:
            metadata["$"] = {
                "kind": "dict",
                "original_count": len(items),
                "returned_count": _FALLBACK_KEY_LIMIT,
            }

        preview[_PREVIEW_KEY] = {
            "truncated": True,
            "original_chars": original_chars,
            "max_chars": max_chars,
            "fallback": True,
            "fields": metadata,
        }
        return preview

    return {
        "value": _summarize_value(output),
        _PREVIEW_KEY: {
            "truncated": True,
            "original_chars": original_chars,
            "max_chars": max_chars,
            "fallback": True,
            "fields": metadata,
        },
    }


def _summarize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            "_preview_type": "dict",
            "key_count": len(value),
            "keys": [str(key)[:64] for key in list(value.keys())[:10]],
        }
    if isinstance(value, list):
        return {
            "_preview_type": "list",
            "item_count": len(value),
        }
    if isinstance(value, str):
        return value[:64]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)[:64]
