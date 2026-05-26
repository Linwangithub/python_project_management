"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

from app.schemas.pspm_common import RoleName

class UserItem(BaseModel):
    """用户管理列表中的单条用户数据。"""
    id: int = Field(..., description='用户ID')
    userid: int = Field(..., description='业务用户ID')
    username: str = Field(..., description='用户名')
    password: str = Field(..., description='密码')
    role: RoleName = Field(..., description='角色')
    operator: str = Field('system', description='操作人')
    created_at: datetime | None = Field(None, description='创建时间')


class UserItems(BaseModel):
    """用户管理分页数据。"""
    total: int = Field(0, description='总数')
    data: List[UserItem] = Field(default_factory=list, description='列表数据')


class UserItemsResponse(base.BaseResponse):
    """用户管理列表接口响应。"""
    data: UserItems


class EnvItem(BaseModel):
    """环境管理列表中的单条环境数据。"""
    id: int = Field(..., description='环境ID')
    env_name: str = Field(..., description='环境名称')
    project_name: str | None = Field(None, description='关联项目')
    python_version: str | None = Field(None, description='Python版本')
    main_packages: str | None = Field(None, description='主要依赖包')
    created_at: datetime | None = Field(None, description='创建时间')


class EnvItems(BaseModel):
    """环境管理分页数据。"""
    total: int = Field(0, description='总数')
    data: List[EnvItem] = Field(default_factory=list, description='列表数据')


class EnvItemsResponse(base.BaseResponse):
    """环境管理列表接口响应。"""
    data: EnvItems


class ServerItem(BaseModel):
    """服务器管理列表中的单条服务器数据。"""
    id: int = Field(..., description='服务器ID')
    alias: str | None = Field(None, description='服务器别名')
    ip: str = Field(..., description='服务器IP')
    root_password: str = Field('', description='Root密码明文')
    users: str = Field('root', description='已分配用户')
    remark: str | None = Field(None, description='备注')


class ServerItems(BaseModel):
    """服务器管理分页数据。"""
    total: int = Field(0, description='总数')
    data: List[ServerItem] = Field(default_factory=list, description='列表数据')


class ServerItemsResponse(base.BaseResponse):
    """服务器管理列表接口响应。"""
    data: ServerItems
