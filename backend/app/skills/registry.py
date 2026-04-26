"""Skill 注册器。"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.skill import SkillDefinition
from app.skills.atomic.conversation_reply import ConversationReplyAtomicSkill
from app.skills.atomic.event_relation import EventRelationAtomicSkill
from app.skills.atomic.format_output import PassageFormatOutputAtomicSkill
from app.skills.atomic.graph_query import GraphQueryAtomicSkill
from app.skills.atomic.graph_write import GraphWriteAtomicSkill
from app.skills.atomic.nl_to_cypher_read import NaturalLanguageToCypherReadAtomicSkill
from app.skills.atomic.passage_meta import PassageMetaAtomicSkill
from app.skills.atomic.person_layer import PersonLayerAtomicSkill
from app.skills.atomic.probe import ProbeAtomicSkill
from app.skills.base import BaseSkill
from app.skills.workflow.conversation_reply_workflow import ConversationReplyWorkflowSkill
from app.skills.workflow.passage_ingestion_workflow import PassageIngestionWorkflowSkill


class SkillRegistry:
    """统一管理 Skill 元数据与脚本实例。"""

    def __init__(self) -> None:
        self._instances: dict[str, BaseSkill] = {
            # 对话相关
            "conversation_reply_atomic": ConversationReplyAtomicSkill(),
            "conversation_reply_workflow": ConversationReplyWorkflowSkill(),
            # 图查询/写入通用
            "graph_query_atomic": GraphQueryAtomicSkill(),
            "graph_write_atomic": GraphWriteAtomicSkill(),
            "nl_to_cypher_read_atomic": NaturalLanguageToCypherReadAtomicSkill(),
            # 功能 A：5 个 atomic + 1 个 workflow
            "probe_atomic": ProbeAtomicSkill(),
            "passage_meta_atomic": PassageMetaAtomicSkill(),
            "person_layer_atomic": PersonLayerAtomicSkill(),
            "event_relation_atomic": EventRelationAtomicSkill(),
            "passage_format_output_atomic": PassageFormatOutputAtomicSkill(),
            "passage_ingestion_workflow": PassageIngestionWorkflowSkill(),
        }

    def get(self, skill_code: str) -> BaseSkill:
        if skill_code not in self._instances:
            raise KeyError(f"skill {skill_code} not found")
        return self._instances[skill_code]

    def register_builtin_metadata(self, db: Session) -> None:
        """把内置 Skill 的元数据写入数据库。"""

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
                "name": "普通对话 Workflow Skill",
                "code": "conversation_reply_workflow",
                "skill_type": "workflow",
                "description": "封装普通对话回复流程。",
                "script_path": str(Path("backend/app/skills/workflow/conversation_reply_workflow.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "图查询 Atomic Skill",
                "code": "graph_query_atomic",
                "skill_type": "atomic",
                "description": "用于执行图查询逻辑。",
                "script_path": str(Path("backend/app/skills/atomic/graph_query.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "图写入 Atomic Skill",
                "code": "graph_write_atomic",
                "skill_type": "atomic",
                "description": "用于执行图写入逻辑，仅管理员可调用。",
                "script_path": str(Path("backend/app/skills/atomic/graph_write.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "自然语言转只读 Cypher Atomic Skill",
                "code": "nl_to_cypher_read_atomic",
                "skill_type": "atomic",
                "description": "把自然语言请求转换为只读 Cypher 并执行图查询。",
                "script_path": str(Path("backend/app/skills/atomic/nl_to_cypher_read.py")),
                "allowed_roles": ["user", "admin"],
            },
            # ===== 功能 A =====
            {
                "name": "Skill-0 探针扫描",
                "code": "probe_atomic",
                "skill_type": "atomic",
                "description": "前置探针：is_female / is_damaged / is_clergy / has_courtesy_name。",
                "script_path": str(Path("backend/app/skills/atomic/probe.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Skill-1 文章基础信息",
                "code": "passage_meta_atomic",
                "skill_type": "atomic",
                "description": "抽取 source_type_code 与 era；写 SQLite passages + Neo4j Passage_Info。",
                "script_path": str(Path("backend/app/skills/atomic/passage_meta.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Skill-2/3/6 人物层",
                "code": "person_layer_atomic",
                "skill_type": "atomic",
                "description": "扫描人物 + 重要度评级 + 人物两两关系候选；写 Neo4j Person_Nodes。",
                "script_path": str(Path("backend/app/skills/atomic/person_layer.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Skill-4/5/7 事件 + 关系码",
                "code": "event_relation_atomic",
                "skill_type": "atomic",
                "description": "生平事件 + 历史事件关联 + 人物关系字母码（≤3 字母）。",
                "script_path": str(Path("backend/app/skills/atomic/event_relation.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Stage 4 渲染输出",
                "code": "passage_format_output_atomic",
                "skill_type": "atomic",
                "description": "把 4 段产物渲染为 YAML 写入 output/*.txt。",
                "script_path": str(Path("backend/app/skills/atomic/format_output.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "古籍处理 Workflow",
                "code": "passage_ingestion_workflow",
                "skill_type": "workflow",
                "description": "功能 A 主流水线：probe → passage_meta → person_layer → event_relation → format_output。",
                "script_path": str(Path("backend/app/skills/workflow/passage_ingestion_workflow.py")),
                "allowed_roles": ["user", "admin"],
            },
        ]

        # 启动时无脑 upsert：让代码端的定义成为唯一真理源，
        # 已有记录会被同步覆盖 allowed_roles_json 等可变字段。
        for definition in definitions:
            allowed_roles_json = json.dumps(
                definition["allowed_roles"], ensure_ascii=False
            )
            existing = (
                db.query(SkillDefinition)
                .filter(SkillDefinition.code == definition["code"])
                .first()
            )
            if existing:
                existing.name = definition["name"]
                existing.skill_type = definition["skill_type"]
                existing.description = definition["description"]
                existing.script_path = definition["script_path"]
                existing.allowed_roles_json = allowed_roles_json
                existing.status = "enabled"
                db.add(existing)
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
                    allowed_roles_json=allowed_roles_json,
                    version="1.0.0",
                    status="enabled",
                )
            )

        db.commit()

    def list_skill_codes(self) -> list[str]:
        return list(self._instances.keys())
