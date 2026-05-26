"""基础 Schema 模块，定义统一响应、分页和通用请求数据结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from abc import ABC
import time
import datetime
from typing import Annotated, Any, Literal, List, Dict, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

Status = Literal["success", "error", "fail"]


class BaseRequest(BaseModel, ABC):
    """所有请求模型的基础类。"""

    model_config = ConfigDict(extra="forbid")


class BaseResponse(BaseModel, ABC):
    """所有响应模型的基础类。

    接口响应可能随版本扩展，模型会忽略未定义的额外字段。
    """
    message: str = Field('success', description='信息')
    status: Status = Field('success', description='状态类型')
    code: int = Field(200, description='状态编码')
    timestamp: int = Field(int(time.time() * 1000), description='时间戳')

    model_config = ConfigDict(extra="ignore")


class Item(BaseModel):
    """单条数据响应模型。"""
    model_config = ConfigDict(extra="ignore")
    # model_config = ConfigDict(from_attributes=True, extra="ignore")


class ItemResponse(BaseResponse):
    """单条数据统一响应模型。"""
    data: Any = Field(None, description='数据')


class Items(BaseModel):
    """列表数据响应模型。"""
    total: int = Field(0, description='数据总数')
    columns: List[Any] = Field([], description='列名')
    data: List[Any] = Field([], description='数据列表')


class ItemsResponse(BaseResponse):
    """列表数据统一响应模型。"""
    data: Items
