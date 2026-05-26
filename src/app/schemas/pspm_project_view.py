"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

from app.schemas.pspm_common import ProjectStatusName

class ProjectItem(BaseModel):
    """项目管理列表中的单条项目数据。"""
    id: int = Field(..., description='项目ID')
    owner_id: int = Field(..., description='所属用户ID')
    owner: str = Field(..., description='所属用户名')

    name: str = Field(..., description='项目名称')
    description: str | None = Field(None, description='项目描述')

    server_id: int | None = Field(None, description='服务器ID')
    server_ip: str | None = Field(None, description='服务器IP')

    backend_path: str | None = Field(None, description='后端代码路径')
    frontend_path: str | None = Field(None, description='前端打包文件路径')
    nginx_conf_path: str | None = Field(None, description='Nginx配置文件路径')
    nginx_server_ip: str | None = Field(None, description='Nginx服务器IP')
    nginx_config_text: str | None = Field(None, description='Nginx server块文本')

    frontend_port: str | None = Field(None, description='Nginx前端端口')
    backend_dev_port: str | None = Field(None, description='后端开发端口')
    backend_deploy_port: str | None = Field(None, description='后端部署端口')

    database_name: str | None = Field(None, description='数据库名称')
    database_host: str | None = Field(None, description='数据库主机')
    database_port: str | None = Field(None, description='数据库端口')
    database_user: str | None = Field(None, description='数据库账号')
    database_password: str | None = Field(None, description='数据库密码')
    conda_env_name: str | None = Field(None, description='Conda环境名称')
    python_version: str | None = Field(None, description='Python版本')

    dev_start_command: str | None = Field(None, description='开发启动命令')
    deploy_start_command: str | None = Field(None, description='部署启动命令')
    entry_file_path: str | None = Field(None, description='项目入口文件路径')

    running_port: str | None = Field(None, description='运行时检测到的端口')
    service_status: str = Field('已停止', description='服务运行状态')
    project_status: str = Field('未检测', description='项目检测状态')
    project_status_detail: str | None = Field('', description='项目检测详情')
    nginx_info: str | None = Field(None, description='Nginx摘要')
    database_info: str | None = Field(None, description='数据库摘要')

    status: ProjectStatusName = Field('已停止', description='状态')
    created_at: datetime | None = Field(None, description='创建时间')


class ProjectItems(BaseModel):
    """项目管理分页数据。"""
    total: int = Field(0, description='总数')
    data: List[ProjectItem] = Field(default_factory=list, description='列表数据')


class ProjectItemsResponse(base.BaseResponse):
    """项目管理列表接口响应。"""
    data: ProjectItems


class ProjectHealthCheckResponse(base.BaseResponse):
    """单个项目健康检测接口响应。"""
    data: ProjectItem


class ProjectDetailField(BaseModel):
    """项目详情侧边栏中的一个字段。"""
    key: str = Field('', description='字段Key')
    label: str = Field(..., description='字段标题')
    value: str = Field('', description='展示值')
    mono: bool = Field(False, description='是否使用等宽字体')
    secret: bool = Field(False, description='是否为敏感字段')


class ProjectDetailSection(BaseModel):
    """项目详情侧边栏中的一个信息分组。"""
    title: str = Field(..., description='分组标题')
    fields: List[ProjectDetailField] = Field(default_factory=list, description='字段列表')


class ProjectDetailData(BaseModel):
    """项目完整详情数据。"""
    project_id: int = Field(..., description='项目ID')
    project_name: str = Field(..., description='项目名称')
    sections: List[ProjectDetailSection] = Field(default_factory=list, description='详情分组')


class ProjectDetailResponse(base.BaseResponse):
    """项目详情接口响应。"""
    data: ProjectDetailData


class ProjectOperationLogItem(BaseModel):
    """项目操作日志列表中的单条日志。"""
    id: int = Field(..., description='日志ID')
    project_id: int = Field(..., description='项目ID')
    operator_id: int | None = Field(None, description='操作人ID')
    operator_name: str = Field('', description='操作人用户名')
    action: str = Field('', description='动作编码')
    action_label: str = Field('', description='动作标题')
    summary: str = Field('', description='摘要')
    before_data: dict[str, Any] | None = Field(None, description='变更前数据')
    after_data: dict[str, Any] | None = Field(None, description='变更后数据')
    detail: dict[str, Any] | None = Field(None, description='详情数据')
    created_at: datetime | None = Field(None, description='创建时间')


class ProjectOperationLogsData(BaseModel):
    """项目操作日志分页数据。"""
    project_id: int = Field(..., description='项目ID')
    project_name: str = Field(..., description='项目名称')
    total: int = Field(0, description='总数')
    data: List[ProjectOperationLogItem] = Field(default_factory=list, description='列表数据')


class ProjectOperationLogsResponse(base.BaseResponse):
    """项目操作日志接口响应。"""
    data: ProjectOperationLogsData
