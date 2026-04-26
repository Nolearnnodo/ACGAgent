"""字典表模型。

供 Skill-1/4/5/7 在抽取过程中查询：
- 年号表：约束 Time.era 取值，并提供朝代/起讫年。
- 历史事件表：检测 Life_Events 时间是否落入某个历史事件区间。
- 文章类型表：源文档类别(0=墓志铭/塔铭, 1=一般历史文献)。
- 关系类型表：人物-人物字母关系码合法性校验。

字典数据通过 Alembic 迁移一次性 seed 进库，业务侧只读不写。
"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class EraDictionary(Base):
    """年号表。

    主键 (era, dynasty) 复合：年号在跨朝代场景下存在重名（中兴/延兴/至德/永泰等），
    通过补充 dynasty 来唯一定位。LLM 抽取时同时输出 era + dynasty。
    """

    __tablename__ = "era_dictionary"

    era: Mapped[str] = mapped_column(String(64), primary_key=True)
    dynasty: Mapped[str] = mapped_column(String(32), primary_key=True)
    start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    simplified: Mapped[str] = mapped_column(String(32), nullable=False)
    traditional: Mapped[str] = mapped_column(String(32), nullable=False)


class HistoricalEventDictionary(Base):
    """历史事件表。"""

    __tablename__ = "historical_event_dictionary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year_label: Mapped[str] = mapped_column(String(64), nullable=False)
    event_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    event_details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SourceTypeDictionary(Base):
    """文章类型表。"""

    __tablename__ = "source_type_dictionary"

    code: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RelationCodeDictionary(Base):
    """人物关系字母码表。"""

    __tablename__ = "relation_code_dictionary"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    meaning: Mapped[str] = mapped_column(String(64), nullable=False)
    direction_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
