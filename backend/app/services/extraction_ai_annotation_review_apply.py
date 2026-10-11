"""把首轮质检判定写回结构化标注，作为可导入的 ground truth。"""

from __future__ import annotations

from typing import Any

from app.schemas.extraction_annotation import (
    EVENT_TYPES,
    ExcludedMention,
    EvidenceSpan,
    ExtractionAnnotationLabel,
    HistoricalEventAnnotation,
    LifeEventAnnotation,
    PersonAnnotation,
    PersonRelationAnnotation,
    SchemaConflict,
    UnresolvedItem,
    AIAnnotationReviewItem,
    AIAnnotationReviewOmission,
)
from app.services.extraction_annotation_service import (
    codepoint_to_utf16_offset,
    drop_meaningless_excluded_mentions,
)


_EMPTY = {"（空）", "空"}
_UNSET = {"未判断"}
_BOOL_TRUE = {"是", "true", "True"}
_BOOL_FALSE = {"否", "false", "False"}
_SOURCE_VALUES = {
    "墓志铭或塔铭类": "epitaph",
    "一般历史文献": "history",
    "暂不能判断": "uncertain",
    "epitaph": "epitaph",
    "history": "history",
    "uncertain": "uncertain",
}
_MATERIAL_VALUES = {
    "标题与正文完整匹配": "complete",
    "存在缺页、错配或转换问题": "issue",
    "暂不能判断": "uncertain",
    "complete": "complete",
    "issue": "issue",
    "uncertain": "uncertain",
}
_CHECK_VALUES = {
    "尚未检查": "unreviewed",
    "已检查且有事实": "has_fact",
    "原文未提及": "not_mentioned",
    "暂不能判断": "uncertain",
    "工具暂不支持": "unsupported",
    "unreviewed": "unreviewed",
    "has_fact": "has_fact",
    "not_mentioned": "not_mentioned",
    "uncertain": "uncertain",
    "unsupported": "unsupported",
}
_FACT_VALUES = {
    "确定": "confirmed",
    "暂不能判断": "uncertain",
    "工具暂不支持": "unsupported",
    "confirmed": "confirmed",
    "uncertain": "uncertain",
    "unsupported": "unsupported",
}
_FIELD_VALUES = {
    "原文明示": "present",
    "原文未提及": "not_mentioned",
    "本项不适用": "not_applicable",
    "暂不能判断": "uncertain",
    "工具暂不支持": "unsupported",
    "present": "present",
    "not_mentioned": "not_mentioned",
    "not_applicable": "not_applicable",
    "uncertain": "uncertain",
    "unsupported": "unsupported",
}


def apply_ai_review_to_label(
    label: ExtractionAnnotationLabel,
    items: list[AIAnnotationReviewItem],
    omissions: list[AIAnnotationReviewOmission],
    context: str,
) -> ExtractionAnnotationLabel:
    """保留判定为正确的 AI 字段，把错误项和可结构化的缺漏写进新标签。"""

    # 即使调用方传入的是旧任务结果，也不能把空占位项带入盲标草稿。
    drop_meaningless_excluded_mentions(label)
    payload = label.model_dump(mode="python")
    for item in items:
        if item.verdict != "incorrect" or item.remove_item:
            continue
        if item.id.startswith("coverage:"):
            continue
        _apply_item(payload, item, context)
    removal_paths = {
        path
        for item in items
        if item.remove_item and (path := review_removal_path(item)) is not None
    }
    _apply_removals(payload, removal_paths)
    _apply_omissions(payload, omissions, context)
    return ExtractionAnnotationLabel.model_validate(payload)


def _apply_item(payload: dict[str, Any], item: AIAnnotationReviewItem, context: str) -> None:
    tokens = item.path.split(".")
    if not tokens:
        return
    if _is_evidence_path(item.path):
        spans = _correction_spans(item, context)
        if not spans:
            return
        dumped = [span.model_dump(mode="python") for span in spans]
        last = tokens[-1]
        if last == "evidence" or last == "mentions":
            _set_path(payload, tokens, dumped)
            return
        _set_path(payload, tokens, dumped[0])
        parent = _get_path(payload, tokens[:-1])
        if isinstance(parent, list) and len(dumped) > 1:
            parent.extend(dumped[1:])
        return
    current = _get_path(payload, tokens)
    parsed = _parse_value(item.path, item.correction, current)
    if parsed is _UNCHANGED:
        return
    _set_path(payload, tokens, parsed)
    if "event_checks" in item.path and parsed == "has_fact":
        _apply_event_check_evidence(payload, tokens, item)


def _apply_event_check_evidence(
    payload: dict[str, Any],
    tokens: list[str],
    item: AIAnnotationReviewItem,
) -> None:
    if len(tokens) != 4 or tokens[0] != "persons" or not tokens[1].isdigit():
        return
    person = _get_path(payload, tokens[:2])
    if not isinstance(person, dict):
        return
    event_type = tokens[-1]
    if event_type not in EVENT_TYPES:
        return

    evidence = [span.model_dump(mode="python") for span in item.correction_evidence]
    life_events = person.setdefault("life_events", [])
    if item.correction_data:
        event_payload = dict(item.correction_data)
        event_payload["event_type"] = event_type
        event_payload["evidence"] = evidence
        all_events = [
            event
            for candidate in payload.get("persons") or []
            if isinstance(candidate, dict)
            for event in candidate.get("life_events") or []
            if isinstance(event, dict)
        ]
        requested_key = str(
            event_payload.get("key")
            or f"review-{person.get('key') or tokens[1]}-{event_type}"
        )
        event_payload["key"] = _unique_key(all_events, requested_key)
        event = LifeEventAnnotation.model_validate(event_payload)
        life_events.append(event.model_dump(mode="python"))
        return

    matching_events = [
        event
        for event in life_events
        if isinstance(event, dict) and event.get("event_type") == event_type
    ]
    if matching_events:
        existing = matching_events[0].setdefault("evidence", [])
        existing_ids = {str(span.get("id") or "") for span in existing if isinstance(span, dict)}
        existing.extend(span for span in evidence if span["id"] not in existing_ids)
        del existing[8:]
        return

    used_keys = {
        str(event.get("key") or "")
        for candidate in payload.get("persons") or []
        if isinstance(candidate, dict)
        for event in candidate.get("life_events") or []
        if isinstance(event, dict)
    }
    person_key = str(person.get("key") or f"person-{tokens[1]}")
    event_number = EVENT_TYPES.index(event_type) + 1
    base_key = f"review-{person_key}-event-{event_number}"[:64]
    event_key = base_key
    suffix = 1
    while event_key in used_keys:
        suffix += 1
        tail = f"-{suffix}"
        event_key = f"{base_key[:64 - len(tail)]}{tail}"

    life_events.append(
        {
            "key": event_key,
            "event_type": event_type,
            "state": "confirmed",
            "time": {"state": "not_applicable" if event_type == "籍贯" else "not_mentioned"},
            "location": {"state": "not_mentioned"},
            "official_title": "",
            "evidence": evidence,
            "note": "首轮质检补录，事件详情待盲标完善。",
        }
    )


_UNCHANGED = object()


def _is_evidence_path(path: str) -> bool:
    return ".mentions." in path or ".evidence." in path or path.endswith(".evidence")


def _correction_spans(item: AIAnnotationReviewItem, context: str) -> list[EvidenceSpan]:
    if item.correction_evidence:
        return list(item.correction_evidence)
    quote = item.correction.strip()
    if not quote:
        return []
    start = context.find(quote)
    if start < 0:
        return []
    end = start + len(quote)
    return [
        EvidenceSpan(
            id=f"review-{item.id}"[:64],
            quote=quote,
            start=start,
            end=end,
            start_utf16=codepoint_to_utf16_offset(context, start),
            end_utf16=codepoint_to_utf16_offset(context, end),
        )
    ]


def review_removal_path(item: AIAnnotationReviewItem) -> str | None:
    """把可删除的质检项映射到原始标签中的完整记录或单条证据。

    质检项的 ``path`` 使用原始数组下标，``id`` 则说明它代表记录字段还是证据字段。
    两者结合后可以阻止把文献属性等单值字段误当成可删除记录。
    """

    parts = item.id.split(":")
    path = item.path.split(".")
    if not parts or not path:
        return None
    if parts[0] == "person" and len(parts) >= 3:
        if parts[2] == "mention" and len(parts) >= 4 and path[-1].isdigit():
            return item.path
        if parts[2] == "event-check":
            return ".".join(path[:2]) if len(path) >= 2 else None
        return ".".join(path[:2]) if len(path) >= 2 else None
    if parts[0] in {"event", "history"} and len(parts) >= 4:
        if parts[3] == "evidence" and len(parts) >= 5 and path[-1].isdigit():
            return item.path
        return ".".join(path[:4]) if len(path) >= 4 else None
    if parts[0] == "relation" and len(parts) >= 3:
        if parts[2] == "evidence" and len(parts) >= 4 and path[-1].isdigit():
            return item.path
        return ".".join(path[:2]) if len(path) >= 2 else None
    if parts[0] == "aux" and len(parts) >= 4:
        if parts[3] == "evidence" and len(parts) >= 5 and path[-1].isdigit():
            return item.path
        return ".".join(path[:2]) if len(path) >= 2 else None
    return None


def _path_tokens(path: str) -> tuple[str | int, ...]:
    return tuple(int(part) if part.isdigit() else part for part in path.split("."))


def _is_ancestor_path(ancestor: tuple[str | int, ...], path: tuple[str | int, ...]) -> bool:
    return len(ancestor) < len(path) and path[: len(ancestor)] == ancestor


def _apply_removals(payload: dict[str, Any], removal_paths: set[str]) -> None:
    if not removal_paths:
        return
    candidates = {_path_tokens(path) for path in removal_paths}
    # 删除整个人物/事件时，其内部证据无需再单独删除；先消除这些重复路径。
    paths = [
        path
        for path in candidates
        if not any(_is_ancestor_path(other, path) for other in candidates)
    ]

    removed_person_keys: set[str] = set()
    removed_event_types: list[tuple[str, str]] = []
    for path in paths:
        try:
            if len(path) == 2 and path[0] == "persons" and isinstance(path[1], int):
                person = payload["persons"][path[1]]
                removed_person_keys.add(str(person.get("key") or ""))
            elif (
                len(path) == 4
                and path[0] == "persons"
                and isinstance(path[1], int)
                and path[2] == "life_events"
                and isinstance(path[3], int)
            ):
                person = payload["persons"][path[1]]
                event = person["life_events"][path[3]]
                removed_event_types.append(
                    (str(person.get("key") or ""), str(event.get("event_type") or ""))
                )
        except (IndexError, KeyError, TypeError):
            continue

    def sort_key(path: tuple[str | int, ...]) -> tuple[int, str, int]:
        parent = ".".join(str(part) for part in path[:-1])
        last = path[-1] if path and isinstance(path[-1], int) else -1
        return len(path), parent, last

    # 更深层路径优先；同一数组从大下标向小下标删除，避免下标漂移。
    for path in sorted(paths, key=sort_key, reverse=True):
        try:
            parent = _get_path(payload, [str(part) for part in path[:-1]])
            last = path[-1]
            if isinstance(parent, list) and isinstance(last, int) and 0 <= last < len(parent):
                parent.pop(last)
        except (IndexError, KeyError, TypeError):
            continue

    if removed_person_keys:
        payload["person_relations"] = [
            relation
            for relation in payload.get("person_relations") or []
            if relation.get("source_person_key") not in removed_person_keys
            and relation.get("target_person_key") not in removed_person_keys
        ]
    for person_key, event_type in removed_event_types:
        person = _find_by_key(payload.get("persons") or [], person_key)
        if person is None:
            continue
        has_same_type = any(
            event.get("event_type") == event_type
            for event in person.get("life_events") or []
        )
        checks = person.setdefault("event_checks", {})
        if not has_same_type and checks.get(event_type) == "has_fact":
            checks[event_type] = "not_mentioned"


def _apply_omissions(
    payload: dict[str, Any],
    omissions: list[AIAnnotationReviewOmission],
    context: str,
) -> None:
    # 新人物必须先建立，后续新增事件/关系才能引用它；新增证据最后执行，
    # 这样同一轮也可以给刚新增的记录继续补证据。
    priority = {
        "person": 0,
        "life_event": 1,
        "historical_event": 1,
        "person_relation": 2,
        "excluded_mention": 2,
        "unresolved_item": 2,
        "schema_conflict": 2,
        "evidence": 3,
        "note": 4,
    }
    for omission in sorted(omissions, key=lambda item: priority.get(item.addition_type, 4)):
        if omission.addition_type == "note":
            _apply_legacy_person_omission(payload, omission, context)
        else:
            _apply_structured_addition(payload, omission)


def _apply_legacy_person_omission(
    payload: dict[str, Any],
    omission: AIAnnotationReviewOmission,
    context: str,
) -> None:
    if omission.category not in {"人物", "人物属性", "人物覆盖"}:
        return
    if not omission.evidence and not omission.correction.strip():
        return
    persons: list[dict[str, Any]] = payload.setdefault("persons", [])
    key = _unique_key(persons, f"o-{omission.id}"[:64])
    name = omission.correction.strip() or (omission.evidence[0].quote if omission.evidence else "")
    mentions = [span.model_dump(mode="python") for span in omission.evidence]
    if not mentions and name:
        start = context.find(name)
        if start >= 0:
            end = start + len(name)
            mentions = [
                {
                    "id": f"{key}-m1"[:64],
                    "source": "context",
                    "quote": name,
                    "start": start,
                    "end": end,
                    "start_utf16": codepoint_to_utf16_offset(context, start),
                    "end_utf16": codepoint_to_utf16_offset(context, end),
                }
            ]
    persons.append(
        PersonAnnotation(
            key=key,
            name_surface=name[:255],
            level=3,
            level_reason=(omission.description or "质检补录人物")[:500],
            mentions=mentions,
            event_checks={event_type: "unreviewed" for event_type in EVENT_TYPES},
        ).model_dump(mode="python")
    )


def _apply_structured_addition(
    payload: dict[str, Any],
    omission: AIAnnotationReviewOmission,
) -> None:
    addition_type = omission.addition_type
    data = dict(omission.data)
    evidence = [span.model_dump(mode="python") for span in omission.evidence]
    persons: list[dict[str, Any]] = payload.setdefault("persons", [])

    if addition_type == "person":
        data["key"] = _unique_key(persons, str(data.get("key") or f"review-person-{omission.id}"))
        data["mentions"] = evidence or list(data.get("mentions") or [])
        person = PersonAnnotation.model_validate(data)
        persons.append(person.model_dump(mode="python"))
        return

    if addition_type in {"life_event", "historical_event"}:
        person = _find_by_key(persons, omission.parent_key)
        if person is None:
            raise ValueError("新增事件引用了不存在的人物")
        if int(person.get("level") or 3) == 3:
            raise ValueError("三级人物不能新增主评测事件")
        collection_name = "life_events" if addition_type == "life_event" else "historical_events"
        collection: list[dict[str, Any]] = person.setdefault(collection_name, [])
        prefix = "review-event" if addition_type == "life_event" else "review-history"
        data["key"] = _unique_key(collection, str(data.get("key") or f"{prefix}-{omission.id}"))
        data["evidence"] = evidence or list(data.get("evidence") or [])
        if addition_type == "life_event":
            event = LifeEventAnnotation.model_validate(data)
            collection.append(event.model_dump(mode="python"))
            person.setdefault("event_checks", {})[event.event_type] = "has_fact"
        else:
            event = HistoricalEventAnnotation.model_validate(data)
            collection.append(event.model_dump(mode="python"))
        return

    if addition_type == "person_relation":
        relations: list[dict[str, Any]] = payload.setdefault("person_relations", [])
        data["key"] = _unique_key(relations, str(data.get("key") or f"review-relation-{omission.id}"))
        data["evidence"] = evidence or list(data.get("evidence") or [])
        relation = PersonRelationAnnotation.model_validate(data)
        person_keys = {str(person.get("key") or "") for person in persons}
        if relation.source_person_key not in person_keys or relation.target_person_key not in person_keys:
            raise ValueError("新增关系引用了不存在的人物")
        relations.append(relation.model_dump(mode="python"))
        return

    auxiliary_models = {
        "excluded_mention": ("excluded_mentions", ExcludedMention, "review-excluded"),
        "unresolved_item": ("unresolved_items", UnresolvedItem, "review-unresolved"),
        "schema_conflict": ("schema_conflicts", SchemaConflict, "review-conflict"),
    }
    if addition_type in auxiliary_models:
        collection_name, model, prefix = auxiliary_models[addition_type]
        collection = payload.setdefault(collection_name, [])
        data["key"] = _unique_key(collection, str(data.get("key") or f"{prefix}-{omission.id}"))
        data["evidence"] = evidence or list(data.get("evidence") or [])
        collection.append(model.model_validate(data).model_dump(mode="python"))
        return

    if addition_type == "evidence":
        target_type = str(data.get("target_type") or "")
        target_key = str(data.get("target_key") or "")
        target = _find_evidence_target(payload, target_type, target_key)
        if target is None:
            raise ValueError("新增证据引用了不存在的记录")
        field_name = "mentions" if target_type == "person" else "evidence"
        existing = target.setdefault(field_name, [])
        existing_ids = {str(item.get("id") or "") for item in existing}
        existing.extend(item for item in evidence if str(item.get("id") or "") not in existing_ids)


def _find_by_key(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get("key") or "") == key), None)


def _unique_key(items: list[dict[str, Any]], requested: str) -> str:
    base = (requested.strip() or "review-item")[:64]
    used = {str(item.get("key") or "") for item in items}
    if base not in used:
        return base
    suffix = 2
    while True:
        candidate = f"{base[: max(1, 63 - len(str(suffix)))]}-{suffix}"
        if candidate not in used:
            return candidate
        suffix += 1


def _find_evidence_target(
    payload: dict[str, Any],
    target_type: str,
    target_key: str,
) -> dict[str, Any] | None:
    if target_type == "person":
        return _find_by_key(payload.get("persons") or [], target_key)
    if target_type in {"life_event", "historical_event"}:
        collection_name = "life_events" if target_type == "life_event" else "historical_events"
        for person in payload.get("persons") or []:
            target = _find_by_key(person.get(collection_name) or [], target_key)
            if target is not None:
                return target
        return None
    collection_names = {
        "person_relation": "person_relations",
        "excluded_mention": "excluded_mentions",
        "unresolved_item": "unresolved_items",
        "schema_conflict": "schema_conflicts",
    }
    collection_name = collection_names.get(target_type)
    if collection_name is None:
        return None
    return _find_by_key(payload.get(collection_name) or [], target_key)


def _get_path(payload: Any, tokens: list[str]) -> Any:
    current = payload
    for part in tokens:
        key: Any = int(part) if part.isdigit() else part
        current = current[key]
    return current


def _set_path(payload: Any, tokens: list[str], value: Any) -> None:
    parent = _get_path(payload, tokens[:-1]) if len(tokens) > 1 else payload
    last = tokens[-1]
    key: Any = int(last) if last.isdigit() else last
    parent[key] = value


def _parse_value(path: str, raw: str, current: Any) -> Any:
    text = (raw or "").strip()
    if not text:
        return _UNCHANGED
    if text in _EMPTY:
        if isinstance(current, list):
            return []
        if isinstance(current, int) or current is None:
            return None
        if isinstance(current, bool):
            return None
        return ""
    if path.endswith("source_type") and text in _SOURCE_VALUES:
        return _SOURCE_VALUES[text]
    if path.endswith("material_status") and text in _MATERIAL_VALUES:
        return _MATERIAL_VALUES[text]
    if path.split(".")[-1] in {"is_female", "is_damaged", "is_clergy", "has_courtesy_name", "outside_dictionary"}:
        if text in _UNSET:
            return None
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
    if path.endswith("level"):
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits in {"1", "2", "3"}:
            return int(digits)
    if "event_checks" in path and text in _CHECK_VALUES:
        return _CHECK_VALUES[text]
    if path.endswith(".state") and text in _FACT_VALUES:
        return _FACT_VALUES[text]
    if path.endswith(".state") and text in _FIELD_VALUES:
        return _FIELD_VALUES[text]
    if path.endswith("titles"):
        return [part.strip() for part in text.replace("\n", "、").split("、") if part.strip()]
    if path.endswith("codes") or path.endswith("reverse_codes"):
        return [ch for ch in text if ch in "FMSDHWZCBO"][:3]
    if path.endswith("era_year") or path.endswith("gregorian_year"):
        try:
            return int(text)
        except ValueError:
            return None
    if isinstance(current, bool):
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
        if text in _UNSET:
            return None
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(text)
        except ValueError:
            return _UNCHANGED
    return text
