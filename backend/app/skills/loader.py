"""Skill 加载器。

当前版本直接复用内置注册器；后续可扩展为从数据库 metadata + 文件路径动态导入。
"""

from app.skills.registry import SkillRegistry


def load_skill_registry() -> SkillRegistry:
    """返回统一 Skill 注册器实例。"""

    return SkillRegistry()
