"""权限 Schema 模块，定义角色、菜单和权限接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from typing import Dict, List

from pydantic import BaseModel, Field

from app.schemas import base


class PermissionGrant(BaseModel):
    """权限授权请求模型。"""
    menu_key: str = Field(..., description='菜单key')
    menu_name: str = Field(..., description='菜单名称')
    actions: List[str] = Field(default_factory=list, description='允许操作keys')


class UserPermissionData(BaseModel):
    """用户权限快照数据模型。"""
    user_id: int = Field(..., description='用户ID')
    username: str = Field(..., description='用户名')
    roles: List[str] = Field(default_factory=list, description='角色keys')
    menus: List[str] = Field(default_factory=list, description='可见菜单keys')
    permissions: Dict[str, List[str]] = Field(default_factory=dict, description='菜单对应操作keys')
    grants: List[PermissionGrant] = Field(default_factory=list, description='菜单+操作详情')


class UserPermissionResponse(base.BaseResponse):
    """用户权限快照统一响应模型。"""
    data: UserPermissionData
