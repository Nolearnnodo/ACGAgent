"""Skill 元数据模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class SkillDefinition(Base):
    """Skill 元数据。

    metadata 存库，script_path 指向具体脚本文件，方便动态装配。
    """

    __tablename__ = "skill_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    skill_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    script_path: Mapped[str] = mapped_column(String(255), nullable=False)
    input_schema_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    output_schema_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    allowed_roles_json: Mapped[str] = mapped_column(Text, default='["user"]', nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="enabled", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SkillRelation(Base):
    """Skill 之间的依赖关系。

    未来支持嵌套 Workflow 或渐进式披露时，可以通过这张表追踪编排关系。
    """

    __tablename__ = "skill_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_skill_id: Mapped[int] = mapped_column(ForeignKey("skill_definitions.id", ondelete="CASCADE"), index=True)
    child_skill_id: Mapped[int] = mapped_column(ForeignKey("skill_definitions.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    step_alias: Mapped[str] = mapped_column(String(64), default="", nullable=False)
