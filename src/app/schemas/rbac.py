from typing import Dict, List

from pydantic import BaseModel, Field

from app.schemas import base


class PermissionGrant(BaseModel):
    menu_key: str = Field(..., description='菜单key')
    menu_name: str = Field(..., description='菜单名称')
    actions: List[str] = Field(default_factory=list, description='允许操作keys')


class UserPermissionData(BaseModel):
    user_id: int = Field(..., description='用户ID')
    username: str = Field(..., description='用户名')
    roles: List[str] = Field(default_factory=list, description='角色keys')
    menus: List[str] = Field(default_factory=list, description='可见菜单keys')
    permissions: Dict[str, List[str]] = Field(default_factory=dict, description='菜单对应操作keys')
    grants: List[PermissionGrant] = Field(default_factory=list, description='菜单+操作详情')


class UserPermissionResponse(base.BaseResponse):
    data: UserPermissionData
