import datetime
from typing import Any, List

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import base


class Base(BaseModel):
    userid: int = Field(..., description='业务用户ID')
    username: str = Field(..., description='用户名')


class Data(Base):
    id: int = Field(..., description='主键ID')
    password: str = Field(..., description='密码（明文）')
    project_base_path: str = Field('/root/project', description='当前用户默认项目基础路径')
    project_root_base_path: str = Field('/root/project', description='root角色默认项目基础路径')
    project_user_base_path_template: str = Field('/home/{username}/project', description='普通用户项目路径模板')
    created_at: datetime.datetime | None = Field(None, description='创建时间')
    updated_at: datetime.datetime | None = Field(None, description='更新时间')
    model_config = ConfigDict(from_attributes=True)


class Create(Base):
    password: str = Field(..., description='密码')


class Update(BaseModel):
    username: str | None = Field(None, description='用户名')
    password: str | None = Field(None, description='密码')


class Item(Data):
    pass


class ItemResponse(base.BaseResponse):
    data: Item


class Items(BaseModel):
    total: int = Field(0, description='数据总数')
    columns: List[Any] = Field(default_factory=list, description='列名')
    data: List[Item] = Field(default_factory=list, description='数据列表')


class ItemsResponse(base.BaseResponse):
    data: Items
