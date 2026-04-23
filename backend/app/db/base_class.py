"""ORM 基类定义。

这个文件只负责声明 Base，避免因为集中导入模型而产生循环引用。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的共同父类。"""
