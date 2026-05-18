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
    """Base class for all request schemas."""

    model_config = ConfigDict(extra="forbid")


class BaseResponse(BaseModel, ABC):
    """Base class for all response schemas.

    Since the API can change, we want to ignore any extra fields that are not
    defined in the schema.
    """
    message: str = Field('success', description='信息')
    status: Status = Field('success', description='状态类型')
    code: int = Field(200, description='状态编码')
    timestamp: int = Field(int(time.time() * 1000), description='时间戳')

    model_config = ConfigDict(extra="ignore")


class Item(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # model_config = ConfigDict(from_attributes=True, extra="ignore")


class ItemResponse(BaseResponse):
    data: Any = Field(None, description='数据')


class Items(BaseModel):
    total: int = Field(0, description='数据总数')
    columns: List[Any] = Field([], description='列名')
    data: List[Any] = Field([], description='数据列表')


class ItemsResponse(BaseResponse):
    data: Items
