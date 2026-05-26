"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

class ProjectSettingUpdate(BaseModel):
    """项目设置保存请求体。"""
    description: str | None = Field(None, description='项目描述')
    conda_env_name: str | None = Field(None, description='Conda环境名称')
    python_version: str | None = Field(None, description='Python版本')
    create_conda_env: bool | None = Field(False, description='确认设置后是否创建新的Conda环境')
    drop_original_conda_env: bool | None = Field(False, description='确认设置后是否删除原Conda环境')
    entry_file_path: str | None = Field(None, description='项目入口文件路径')
    backend_dev_port: str | None = Field(None, description='后端开发端口')
    backend_deploy_port: str | None = Field(None, description='后端部署端口')
    frontend_port: str | None = Field(None, description='前端端口')
    dev_start_command: str | None = Field(None, description='开发启动命令')
    deploy_start_command: str | None = Field(None, description='部署启动命令')
    nginx_enabled: bool | None = Field(None, description='该项目是否启用Nginx')
    nginx_server_ip: str | None = Field(None, description='Nginx服务器IP')
    nginx_conf_path: str | None = Field(None, description='Nginx配置文件路径')
    nginx_config_text: str | None = Field(None, description='Nginx server块文本')
    drop_original_nginx_config: bool | None = Field(False, description='确认设置后是否删除原Nginx server块')
    database_name: str | None = Field(None, description='数据库名称')
    database_host: str | None = Field(None, description='数据库主机')
    database_port: str | None = Field(None, description='数据库端口')
    database_user: str | None = Field(None, description='数据库账号')
    database_password: str | None = Field(None, description='数据库密码')
    drop_original_database: bool | None = Field(False, description='确认设置后是否删除原项目数据库')


class ProjectEntryPathNode(BaseModel):
    """项目入口文件选择器中的目录或文件节点。"""
    label: str = Field(..., description='节点标题')
    value: str = Field(..., description='相对路径值')
    leaf: bool = Field(False, description='该节点是否为叶子文件')


class ProjectEntryPathChildrenResponse(base.BaseResponse):
    """项目入口文件子节点接口响应。"""
    data: List[ProjectEntryPathNode] = Field(default_factory=list, description='子节点列表')


class ProjectCondaEnvListData(BaseModel):
    """Conda 环境列表数据。"""
    envs_dir: str = Field('', description='Conda环境目录')
    envs: List[str] = Field(default_factory=list, description='Conda环境名称列表')


class ProjectCondaEnvListResponse(base.BaseResponse):
    """Conda 环境列表接口响应。"""
    data: ProjectCondaEnvListData = Field(default_factory=ProjectCondaEnvListData, description='Conda环境列表数据')
