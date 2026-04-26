"""字典查询 service。

字典表数据由 Alembic seed 灌入，运行期纯只读。
所有查询接口都走进程内缓存：第一次访问时一次性把整张表读入内存，
后续命中缓存。字典数据如需更新，需重启进程或显式调用 reset_cache。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

# 触发所有 ORM 模型集中注册：dictionary_service 既可能从 Skill 内部
# 调用，也可能在测试中直接被 import；需要确保 SQLAlchemy mapper
# 配置阶段能解析跨表 relationship 字符串引用。
from app.db import base as _ensure_models  # noqa: F401
from app.db.session import SessionLocal
from app.models.dictionary import (
    EraDictionary,
    HistoricalEventDictionary,
    RelationCodeDictionary,
    SourceTypeDictionary,
)


@dataclass(frozen=True, slots=True)
class EraEntry:
    era: str
    dynasty: str
    start_year: int
    end_year: int
    simplified: str
    traditional: str


@dataclass(frozen=True, slots=True)
class HistoricalEventEntry:
    id: int
    year_label: str
    event_name: str
    event_details: str
    start_year: int | None
    end_year: int | None


@dataclass(frozen=True, slots=True)
class RelationCodeEntry:
    code: str
    meaning: str
    direction_hint: str


_cache: dict[str, object] = {}


def _with_session(getter):
    """在没有外部传入 session 时，自动开一个独立 session 读字典。"""

    def wrapper(db: Session | None = None):
        if db is not None:
            return getter(db)
        with SessionLocal() as own:
            return getter(own)

    return wrapper


@_with_session
def get_era_entries(db: Session) -> list[EraEntry]:
    if "era_entries" in _cache:
        return _cache["era_entries"]  # type: ignore[return-value]
    rows = db.query(EraDictionary).all()
    entries = [
        EraEntry(
            era=row.era,
            dynasty=row.dynasty,
            start_year=row.start_year,
            end_year=row.end_year,
            simplified=row.simplified,
            traditional=row.traditional,
        )
        for row in rows
    ]
    _cache["era_entries"] = entries
    return entries


def get_era_set(db: Session | None = None) -> set[str]:
    """返回所有 era 名称（不区分朝代去重）。"""

    return {e.era for e in get_era_entries(db)}


def match_era(
    era_text: str,
    dynasty: str | None = None,
    db: Session | None = None,
) -> EraEntry | None:
    """按 era 字面量匹配；若 era 名跨朝代重名，则需要传入 dynasty 以唯一定位。"""

    if not era_text:
        return None
    normalized = era_text.strip()
    candidates = [
        e
        for e in get_era_entries(db)
        if normalized in (e.era, e.simplified, e.traditional)
    ]
    if not candidates:
        return None
    if dynasty:
        for c in candidates:
            if c.dynasty == dynasty:
                return c
    if len(candidates) == 1:
        return candidates[0]
    return None  # 多义且未指定朝代


@_with_session
def get_historical_events(db: Session) -> list[HistoricalEventEntry]:
    if "historical_events" in _cache:
        return _cache["historical_events"]  # type: ignore[return-value]
    rows = db.query(HistoricalEventDictionary).all()
    entries = [
        HistoricalEventEntry(
            id=row.id,
            year_label=row.year_label,
            event_name=row.event_name,
            event_details=row.event_details,
            start_year=row.start_year,
            end_year=row.end_year,
        )
        for row in rows
    ]
    _cache["historical_events"] = entries
    return entries


def get_historical_event_set(db: Session | None = None) -> set[str]:
    return {entry.event_name for entry in get_historical_events(db)}


def find_overlapping_events(
    target_year: int | None,
    db: Session | None = None,
) -> list[HistoricalEventEntry]:
    """返回与给定年份重叠的所有历史事件（简单区间包含判定）。"""

    if target_year is None:
        return []
    overlaps: list[HistoricalEventEntry] = []
    for entry in get_historical_events(db):
        if entry.start_year is None or entry.end_year is None:
            continue
        if entry.start_year <= target_year <= entry.end_year:
            overlaps.append(entry)
    return overlaps


@_with_session
def get_source_types(db: Session) -> dict[int, str]:
    if "source_types" in _cache:
        return _cache["source_types"]  # type: ignore[return-value]
    rows = db.query(SourceTypeDictionary).all()
    entries = {row.code: row.label for row in rows}
    _cache["source_types"] = entries
    return entries


@_with_session
def get_relation_codes(db: Session) -> dict[str, RelationCodeEntry]:
    if "relation_codes" in _cache:
        return _cache["relation_codes"]  # type: ignore[return-value]
    rows = db.query(RelationCodeDictionary).all()
    entries = {
        row.code: RelationCodeEntry(
            code=row.code,
            meaning=row.meaning,
            direction_hint=row.direction_hint,
        )
        for row in rows
    }
    _cache["relation_codes"] = entries
    return entries


def get_relation_code_set(db: Session | None = None) -> set[str]:
    return set(get_relation_codes(db).keys())


def is_valid_relation_chain(codes: Iterable[str], max_chain_len: int = 3, db: Session | None = None) -> bool:
    """校验关系字母码链是否合法。"""

    valid = get_relation_code_set(db)
    chain = list(codes)
    if not chain:
        return False
    if len(chain) > max_chain_len:
        return False
    for ch in chain:
        if ch not in valid:
            return False
    return True


def reset_cache() -> None:
    """测试或字典刷新时清缓存。"""

    _cache.clear()
