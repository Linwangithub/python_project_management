"""模型基类模块，定义所有 ORM 模型复用的基础字段和声明式基类。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from sqlalchemy import DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import DeclarativeBase, declared_attr
from app.core.deps import get_settings, get_helpers


class Base(DeclarativeBase):
    """基础数据模型。

    不同模块中分别作为 ORM 基类或 Pydantic 基础结构使用。
    """
    id: Mapped[int] = mapped_column(primary_key=True, index=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(),
        server_default=func.now(),
        nullable=False,
        comment='创建日期'
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
        comment='更新日期'
    )

    @declared_attr
    def __tablename__(self) -> str:
        """
        生成表名，表名由数据库前缀（如果存在）和类名的蛇形命名组成。
        """
        prefix = get_settings().db.prefix if get_settings().db.prefix else ''
        snake_case_name = get_helpers().pascal_case_to_snake_case(self.__name__)
        return f'{prefix}{snake_case_name}'
