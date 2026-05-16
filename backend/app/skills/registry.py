"""Skill registry."""

from __future__ import annotations

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
from app.skills.atomic.person_exact_match_merge import PersonExactMatchMergeAtomicSkill
from app.skills.atomic.person_layer import PersonLayerAtomicSkill
from app.skills.atomic.probe import ProbeAtomicSkill
from app.skills.base import BaseSkill
from app.skills.workflow.conversation_reply_workflow import ConversationReplyWorkflowSkill
from app.skills.workflow.passage_ingestion_workflow import PassageIngestionWorkflowSkill


class SkillRegistry:
    """Central registry for skill instances and persisted metadata."""

    def __init__(self) -> None:
        self._instances: dict[str, BaseSkill] = {
            "conversation_reply_atomic": ConversationReplyAtomicSkill(),
            "conversation_reply_workflow": ConversationReplyWorkflowSkill(),
            "graph_query_atomic": GraphQueryAtomicSkill(),
            "graph_write_atomic": GraphWriteAtomicSkill(),
            "nl_to_cypher_read_atomic": NaturalLanguageToCypherReadAtomicSkill(),
            "probe_atomic": ProbeAtomicSkill(),
            "passage_meta_atomic": PassageMetaAtomicSkill(),
            "person_layer_atomic": PersonLayerAtomicSkill(),
            "event_relation_atomic": EventRelationAtomicSkill(),
            "passage_format_output_atomic": PassageFormatOutputAtomicSkill(),
            "person_exact_match_merge_atomic": PersonExactMatchMergeAtomicSkill(),
            "passage_ingestion_workflow": PassageIngestionWorkflowSkill(),
        }

    def get(self, skill_code: str) -> BaseSkill:
        if skill_code not in self._instances:
            raise KeyError(f"skill {skill_code} not found")
        return self._instances[skill_code]

    def register_builtin_metadata(self, db: Session) -> None:
        """Upsert built-in skill metadata into SQLite."""

        definitions = [
            {
                "name": "Conversation reply atomic",
                "code": "conversation_reply_atomic",
                "skill_type": "atomic",
                "description": "Generate a normal conversation reply.",
                "script_path": str(Path("backend/app/skills/atomic/conversation_reply.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Conversation reply workflow",
                "code": "conversation_reply_workflow",
                "skill_type": "workflow",
                "description": "Wrap the normal conversation reply flow.",
                "script_path": str(Path("backend/app/skills/workflow/conversation_reply_workflow.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Graph query atomic",
                "code": "graph_query_atomic",
                "skill_type": "atomic",
                "description": "Execute a Neo4j read query.",
                "script_path": str(Path("backend/app/skills/atomic/graph_query.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Graph write atomic",
                "code": "graph_write_atomic",
                "skill_type": "atomic",
                "description": "Execute a Neo4j write query.",
                "script_path": str(Path("backend/app/skills/atomic/graph_write.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Natural-language read-only Cypher atomic",
                "code": "nl_to_cypher_read_atomic",
                "skill_type": "atomic",
                "description": "Convert a user request to read-only Cypher and execute it.",
                "script_path": str(Path("backend/app/skills/atomic/nl_to_cypher_read.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function A probe",
                "code": "probe_atomic",
                "skill_type": "atomic",
                "description": "Extract quick document flags for downstream prompts.",
                "script_path": str(Path("backend/app/skills/atomic/probe.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function A passage metadata",
                "code": "passage_meta_atomic",
                "skill_type": "atomic",
                "description": "Extract source type and era, then write Passage_Info.",
                "script_path": str(Path("backend/app/skills/atomic/passage_meta.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function A person layer",
                "code": "person_layer_atomic",
                "skill_type": "atomic",
                "description": "Extract people, levels, and relationship candidates.",
                "script_path": str(Path("backend/app/skills/atomic/person_layer.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function A event and relation layer",
                "code": "event_relation_atomic",
                "skill_type": "atomic",
                "description": "Extract life events, historical-event links, and person relation codes.",
                "script_path": str(Path("backend/app/skills/atomic/event_relation.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function A format output",
                "code": "passage_format_output_atomic",
                "skill_type": "atomic",
                "description": "Render extraction results to an output YAML text file.",
                "script_path": str(Path("backend/app/skills/atomic/format_output.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Function B exact-name person merge",
                "code": "person_exact_match_merge_atomic",
                "skill_type": "atomic",
                "description": "Merge current-passage people into earlier same-name people without LLM adjudication.",
                "script_path": str(Path("backend/app/skills/atomic/person_exact_match_merge.py")),
                "allowed_roles": ["user", "admin"],
            },
            {
                "name": "Passage ingestion workflow",
                "code": "passage_ingestion_workflow",
                "skill_type": "workflow",
                "description": "Run Function A extraction and Function B exact-name merge.",
                "script_path": str(Path("backend/app/skills/workflow/passage_ingestion_workflow.py")),
                "allowed_roles": ["user", "admin"],
            },
        ]

        for definition in definitions:
            allowed_roles_json = json.dumps(definition["allowed_roles"], ensure_ascii=False)
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
