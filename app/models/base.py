from datetime import datetime
from sqlalchemy import DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import DeclarativeBase, declared_attr
from app.core.deps import get_settings, get_helpers


class Base(DeclarativeBase):
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
