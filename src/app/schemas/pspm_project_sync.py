"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

from app.schemas.pspm_project_check import ProjectDatabaseCheckResponseData
from app.schemas.pspm_project_setting import ProjectEntryPathNode

class ProjectSyncPathChildrenRequest(BaseModel):
    """同步已有项目时查询项目目录子项的请求体。"""
    server_ip: str = Field(..., description='服务器IP')
    rel_path: str = Field('', description='配置的项目基础路径下的相对路径')


class ProjectSyncPathNode(BaseModel):
    """同步已有项目目录选择器节点。"""
    label: str = Field(..., description='展示标题')
    value: str = Field(..., description='相对路径值')
    abs_path: str = Field(..., description='服务器上的绝对路径')
    leaf: bool = Field(False, description='节点是否为叶子节点')


class ProjectSyncPathChildrenResponse(base.BaseResponse):
    """同步已有项目目录子项接口响应。"""
    data: List[ProjectSyncPathNode] = Field(default_factory=list, description='目录节点列表')


class ProjectSyncEntryPathChildrenRequest(BaseModel):
    """同步已有项目时查询入口文件子项的请求体。"""
    server_ip: str = Field(..., description='服务器IP')
    backend_path: str = Field(..., description='已存在的项目目录')
    rel_path: str = Field('', description='已选择项目目录下的相对路径')


class ProjectSyncEntryPathChildrenResponse(base.BaseResponse):
    """同步已有项目入口文件子项接口响应。"""
    data: List[ProjectEntryPathNode] = Field(default_factory=list, description='入口文件节点列表')


class ProjectSyncCondaEnvListRequest(BaseModel):
    """同步已有项目时查询服务器 Conda 环境列表的请求体。"""
    server_ip: str = Field(..., description='服务器IP')


class ProjectSyncCondaEnvListData(BaseModel):
    """同步已有项目 Conda 环境列表数据。"""
    envs_dir: str = Field('', description='Conda环境目录')
    envs: List[str] = Field(default_factory=list, description='Conda环境名称列表')


class ProjectSyncCondaEnvListResponse(base.BaseResponse):
    """同步已有项目 Conda 环境列表接口响应。"""
    data: ProjectSyncCondaEnvListData = Field(default_factory=ProjectSyncCondaEnvListData, description='Conda环境列表数据')


class ProjectSyncCondaCheckRequest(BaseModel):
    """同步已有项目时检查 Conda 环境的请求体。"""
    server_ip: str = Field(..., description='服务器IP')
    conda_env_name: str = Field(..., description='Conda环境名称')


class ProjectSyncCondaCheckData(BaseModel):
    """同步已有项目 Conda 检查结果。"""
    ok: bool = Field(False, description='Conda环境是否存在且Python版本已检测')
    env_name: str = Field('', description='Conda环境名称')
    env_path: str = Field('', description='Conda环境路径')
    python_version: str = Field('', description='实际Python版本')
    message: str = Field('', description='检查消息')


class ProjectSyncCondaCheckResponse(base.BaseResponse):
    """同步已有项目 Conda 检查接口响应。"""
    data: ProjectSyncCondaCheckData


class ProjectSyncDatabaseCheckRequest(BaseModel):
    """同步已有项目时检查数据库连接的请求体。"""
    host: str = Field(..., description='数据库主机')
    port: int = Field(..., description='数据库端口')
    username: str = Field(..., description='数据库账号')
    password: str = Field('', description='数据库密码')
    database_name: str = Field('', description='必须已存在的可选数据库名称')


class ProjectSyncDatabaseCheckData(ProjectDatabaseCheckResponseData):
    """同步已有项目数据库连接检查结果。"""
    databases: list[str] = Field(default_factory=list, description='可见数据库名称列表')


class ProjectSyncDatabaseCheckResponse(base.BaseResponse):
    """同步已有项目数据库检查接口响应。"""
    data: ProjectSyncDatabaseCheckData


class ProjectSyncNginxServerBlockCheckRequest(BaseModel):
    """同步已有项目时检查 Nginx server 块是否匹配的请求体。"""
    server_ip: str = Field(..., description='项目服务器IP')
    nginx_server_ip: str = Field('', description='Nginx服务器IP')
    nginx_conf_path: str = Field(..., description='已存在的Nginx配置文件路径')
    frontend_port: str = Field(..., description='Nginx前端listen端口')
    backend_deploy_port: str = Field(..., description='proxy_pass中的后端部署端口')


class ProjectSyncNginxServerBlockCheckData(BaseModel):
    """同步已有项目 Nginx server 块检查结果。"""
    ok: bool = Field(False, description='是否存在匹配的server块')
    nginx_config_text: str = Field('', description='匹配到的server块文本')
    message: str = Field('', description='检查消息')


class ProjectSyncNginxServerBlockCheckResponse(base.BaseResponse):
    """同步已有项目 Nginx server 块检查接口响应。"""
    data: ProjectSyncNginxServerBlockCheckData


class ProjectSyncNginxServerPortOptionsRequest(BaseModel):
    """同步已有项目时查询已有 Nginx server 端口选项的请求体。"""
    server_ip: str = Field(..., description='项目服务器IP')
    nginx_server_ip: str = Field('', description='Nginx服务器IP')
    nginx_conf_path: str = Field(..., description='已存在的Nginx配置文件路径')


class ProjectSyncNginxServerPortOption(BaseModel):
    """同步已有项目可选择的一组 Nginx 前端端口和后端代理端口。"""
    label: str = Field('', description='展示标题')
    frontend_port: str = Field('', description='Nginx listen端口')
    backend_deploy_port: str = Field('', description='proxy_pass后端端口')
    server_name: str = Field('', description='server_name值')
    nginx_config_text: str = Field('', description='匹配到的server块文本')


class ProjectSyncNginxServerPortOptionsData(BaseModel):
    """同步已有项目 Nginx server 端口选项数据。"""
    options: List[ProjectSyncNginxServerPortOption] = Field(default_factory=list, description='端口选项列表')


class ProjectSyncNginxServerPortOptionsResponse(base.BaseResponse):
    """同步已有项目 Nginx server 端口选项接口响应。"""
    data: ProjectSyncNginxServerPortOptionsData = Field(default_factory=ProjectSyncNginxServerPortOptionsData)


class ProjectSyncRequest(BaseModel):
    """同步已有项目最终提交请求体。"""
    server_ip: str = Field(..., description='项目服务器IP')
    name: str = Field(..., description='项目名称')
    description: str = Field('', description='项目描述')
    backend_path: str = Field(..., description='已存在的项目目录')
    entry_file_path: str = Field('', description='入口文件绝对路径')
    conda_env_name: str = Field(..., description='已存在的Conda环境名称')
    python_version: str = Field('', description='检测到的Python版本')
    use_database: bool = Field(False, description='是否绑定已有数据库')
    database_name: str = Field('', description='已存在的数据库名称')
    database_host: str = Field('', description='数据库主机')
    database_port: int | None = Field(None, description='数据库端口')
    database_user: str = Field('', description='数据库账号')
    database_password: str = Field('', description='数据库密码')
    use_nginx: bool = Field(False, description='是否绑定已有Nginx配置')
    nginx_server_ip: str = Field('', description='Nginx服务器IP')
    nginx_conf_path: str = Field('', description='已存在的Nginx配置文件路径')
    frontend_port: str = Field('', description='Nginx前端端口')
    backend_deploy_port: str = Field('', description='proxy_pass中的后端部署端口')
    nginx_config_text: str = Field('', description='Nginx server块文本')


class ProjectSyncResponseData(BaseModel):
    """同步已有项目结果数据。"""
    project_id: int = Field(..., description='项目ID')
    status: str = Field('同步成功', description='同步状态')
    backend_path: str = Field(..., description='已存在的项目目录')
    conda_env_name: str = Field(..., description='Conda环境名称')
    python_version: str = Field('', description='Python版本')


class ProjectSyncResponse(base.BaseResponse):
    """同步已有项目接口响应。"""
    data: ProjectSyncResponseData
