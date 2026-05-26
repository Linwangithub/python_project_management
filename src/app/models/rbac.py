"""权限模型模块，定义角色、菜单、权限及其关系表结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from typing import Optional

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RbacRole(Base):
    """RBAC 角色 ORM 模型。"""
    __table_args__ = (UniqueConstraint('role_key', name='uq_rbac_role_role_key'),)

    role_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment='角色key，如root/user')
    role_name: Mapped[str] = mapped_column(String(64), nullable=False, comment='角色名称')
    description: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment='角色描述')
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment='状态：-1删除，1正常')


class RbacPermission(Base):
    """RBAC 权限 ORM 模型。"""
    __table_args__ = (UniqueConstraint('permission_key', name='uq_rbac_permission_permission_key'),)

    permission_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False, comment='权限key，如project_management:start_foreground')
    menu_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment='菜单key')
    menu_name: Mapped[str] = mapped_column(String(64), nullable=False, comment='菜单名称')
    action_key: Mapped[Optional[str]] = mapped_column(String(64), index=True, default=None, comment='操作key，空代表菜单可见')
    action_name: Mapped[Optional[str]] = mapped_column(String(64), default=None, comment='操作名称')
    description: Mapped[Optional[str]] = mapped_column(Text, default=None, comment='权限描述')
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment='状态：-1删除，1正常')


class RbacUserRole(Base):
    """用户与角色关系 ORM 模型。"""
    __table_args__ = (UniqueConstraint('user_id', 'role_id', name='uq_rbac_user_role_user_role'),)

    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, comment='用户主键ID')
    role_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, comment='角色主键ID')
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment='状态：-1删除，1正常')


class RbacRolePermission(Base):
    """角色与权限关系 ORM 模型。"""
    __table_args__ = (UniqueConstraint('role_id', 'permission_id', name='uq_rbac_role_permission_role_perm'),)

    role_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, comment='角色主键ID')
    permission_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, comment='权限主键ID')
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment='状态：-1删除，1正常')
