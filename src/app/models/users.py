"""用户模型模块，定义系统用户表结构和用户基础属性。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Users(Base):
    """用户 ORM 模型。"""
    userid: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False, comment='业务用户ID')
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment='用户名')
    password: Mapped[str] = mapped_column(String(255), nullable=False, comment='密码（明文存储）')
