"""用户 Schema 模块，定义用户创建、登录和展示相关数据结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import datetime
from typing import Any, List

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import base
from app.utils.pspm.project_config import ROOT_PROJECT_BASE_DIR, USER_PROJECT_BASE_PATH_TEMPLATE


class Base(BaseModel):
    """基础数据模型。

    不同模块中分别作为 ORM 基类或 Pydantic 基础结构使用。
    """
    userid: int = Field(..., description='业务用户ID')
    username: str = Field(..., description='用户名')


class Data(Base):
    """Data 数据结构。

    用于接口、数据库或业务层传递结构化数据。
    """
    id: int = Field(..., description='主键ID')
    password: str = Field(..., description='密码（明文）')
    project_base_path: str = Field(ROOT_PROJECT_BASE_DIR, description='当前用户默认项目基础路径')
    project_root_base_path: str = Field(ROOT_PROJECT_BASE_DIR, description='root角色默认项目基础路径')
    project_user_base_path_template: str = Field(USER_PROJECT_BASE_PATH_TEMPLATE, description='普通用户项目路径模板')
    created_at: datetime.datetime | None = Field(None, description='创建时间')
    updated_at: datetime.datetime | None = Field(None, description='更新时间')
    model_config = ConfigDict(from_attributes=True)


class Create(Base):
    """Create 数据结构。

    用于接口、数据库或业务层传递结构化数据。
    """
    password: str = Field(..., description='密码')


class Update(BaseModel):
    """Update 数据结构。

    用于接口、数据库或业务层传递结构化数据。
    """
    username: str | None = Field(None, description='用户名')
    password: str | None = Field(None, description='密码')


class Item(Data):
    """单条数据响应模型。"""
    pass


class ItemResponse(base.BaseResponse):
    """单条数据统一响应模型。"""
    data: Item


class Items(BaseModel):
    """列表数据响应模型。"""
    total: int = Field(0, description='数据总数')
    columns: List[Any] = Field(default_factory=list, description='列名')
    data: List[Item] = Field(default_factory=list, description='数据列表')


class ItemsResponse(base.BaseResponse):
    """列表数据统一响应模型。"""
    data: Items
