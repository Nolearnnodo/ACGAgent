"""Skill 注册器。"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.skill import SkillDefinition
from app.skills.atomic.conversation_reply import ConversationReplyAtomicSkill
from app.skills.atomic.graph_query import GraphQueryAtomicSkill
from app.skills.atomic.graph_write import GraphWriteAtomicSkill
from app.skills.atomic.passage_preprocess import PassagePreprocessAtomicSkill
from app.skills.atomic.passage_store_graph import PassageStoreGraphAtomicSkill
from app.skills.atomic.passage_store_sqlite import PassageStoreSqliteAtomicSkill
from app.skills.base import BaseSkill
from app.skills.workflow.conversation_reply_workflow import ConversationReplyWorkflowSkill
from app.skills.workflow.passage_ingestion_workflow import PassageIngestionWorkflowSkill


class SkillRegistry:
    """统一管理 Skill 元数据与脚本实例。"""

    def __init__(self) -> None:
        self._instances: dict[str, BaseSkill] = {
            "conversation_reply_atomic": ConversationReplyAtomicSkill(),
            "graph_query_atomic": GraphQueryAtomicSkill(),
            "graph_write_atomic": GraphWriteAtomicSkill(),
            "conversation_reply_workflow": ConversationReplyWorkflowSkill(),
            "passage_preprocess_atomic": PassagePreprocessAtomicSkill(),
            "passage_store_sqlite_atomic": PassageStoreSqliteAtomicSkill(),
            "passage_store_graph_atomic": PassageStoreGraphAtomicSkill(),
            "passage_ingestion_workflow": PassageIngestionWorkflowSkill(),
        }

    def get(self, skill_code: str) -> BaseSkill:
        """按编码获取 Skill 实例。"""

        if skill_code not in self._instances:
            raise KeyError(f"skill {skill_code} not found")
        return self._instances[skill_code]

    def register_builtin_metadata(self, db: Session) -> None:
        """把内置 Skill 的元数据写入数据库。

        这样后续 Planner / 管理接口 都能围绕统一元数据工作。
        """

        definitions = [
            {
                "name": "普通对话回复 Atomic Skill",
                "code": "conversation_reply_atomic",
                "skill_type": "atomic",
                "description": "用于生成普通文本回复。",
                "script_path": str(Path("backend/app/skills/atomic/conversation_reply.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "图查询 Atomic Skill",
                "code": "graph_query_atomic",
                "skill_type": "atomic",
                "description": "用于执行图查询占位逻辑。",
                "script_path": str(Path("backend/app/skills/atomic/graph_query.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "图写入 Atomic Skill",
                "code": "graph_write_atomic",
                "skill_type": "atomic",
                "description": "用于执行图写入占位逻辑，仅管理员可调用。",
                "script_path": str(Path("backend/app/skills/atomic/graph_write.py")),
                "allowed_roles": ["admin"],
            },
            {
                "name": "普通对话 Workflow Skill",
                "code": "conversation_reply_workflow",
                "skill_type": "workflow",
                "description": "封装普通对话回复流程。",
                "script_path": str(Path("backend/app/skills/workflow/conversation_reply_workflow.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "古籍预处理 Atomic Skill",
                "code": "passage_preprocess_atomic",
                "skill_type": "atomic",
                "description": "对古籍正文做最小清洗。",
                "script_path": str(Path("backend/app/skills/atomic/passage_preprocess.py")),
                "allowed_roles": ["admin"],
            },
            {
                "name": "古籍 SQLite 持久化确认 Atomic Skill",
                "code": "passage_store_sqlite_atomic",
                "skill_type": "atomic",
                "description": "确认古籍正文已写入 SQLite。",
                "script_path": str(Path("backend/app/skills/atomic/passage_store_sqlite.py")),
                "allowed_roles": ["admin"],
            },
            {
                "name": "古籍图数据库入库 Atomic Skill",
                "code": "passage_store_graph_atomic",
                "skill_type": "atomic",
                "description": "执行古籍图数据库入库占位逻辑。",
                "script_path": str(Path("backend/app/skills/atomic/passage_store_graph.py")),
                "allowed_roles": ["admin"],
            },
            {
                "name": "古籍处理 Workflow Skill",
                "code": "passage_ingestion_workflow",
                "skill_type": "workflow",
                "description": "封装古籍上传或录入后的固定处理流程。",
                "script_path": str(Path("backend/app/skills/workflow/passage_ingestion_workflow.py")),
                "allowed_roles": ["admin"],
            },
        ]

        for definition in definitions:
            existing = db.query(SkillDefinition).filter(SkillDefinition.code == definition["code"]).first()
            if existing:
                continue

            db.add(
                SkillDefinition(
                    name=definition["name"],
                    code=definition["code"],
                    skill_type=definition["skill_type"],
                    description=definition["description"],
                    script_path=definition["script_path"],
                    input_schema_json="{}",
                    output_schema_json="{}",
                    allowed_roles_json=json.dumps(definition["allowed_roles"], ensure_ascii=False),
                    version="1.0.0",
                    status="enabled",
                )
            )

        db.commit()

    def list_skill_codes(self) -> list[str]:
        """列出已注册 Skill。"""

        return list(self._instances.keys())
