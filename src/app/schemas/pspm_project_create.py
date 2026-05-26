"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

from app.schemas.pspm_common import RoleName

class UserCreate(BaseModel):
    """创建用户请求体。"""
    username: str = Field(..., description='用户名')
    password: str = Field(..., description='密码')
    role: RoleName = Field('user', description='角色')


class EnvCreate(BaseModel):
    """创建环境请求体。"""
    env_name: str = Field(..., description='环境名称')
    project_name: str | None = Field(None, description='关联项目')
    python_version: str | None = Field(None, description='Python版本')
    main_packages: str | None = Field(None, description='主要依赖包')


class EnvUpdate(BaseModel):
    """更新环境请求体。"""
    project_name: str | None = Field(None, description='关联项目')
    python_version: str | None = Field(None, description='Python版本')
    main_packages: str | None = Field(None, description='主要依赖包')


class ServerCreate(BaseModel):
    """新增服务器请求体。"""
    alias: str | None = Field(None, description='服务器别名')
    ip: str = Field(..., description='服务器IP')
    root_password: str | None = Field(None, description='Root密码明文')
    remark: str | None = Field(None, description='备注')


class ServerUserCreate(BaseModel):
    """服务器新增 Linux 用户请求体。"""
    server_id: int = Field(..., description='服务器ID')
    username: str = Field(..., description='Linux用户名')


class ServerUserDelete(BaseModel):
    """服务器删除 Linux 用户请求体。"""
    server_id: int = Field(..., description='服务器ID')
    username: str = Field(..., description='Linux用户名')


class ProjectCreate(BaseModel):
    """创建项目基础记录请求体。"""
    name: str = Field(..., description='项目名称')
    description: str | None = Field(None, description='项目描述')
    server_id: int | None = Field(None, description='服务器ID')

    backend_path: str | None = Field(None, description='后端代码路径')
    frontend_path: str | None = Field(None, description='前端打包文件路径')
    nginx_conf_path: str | None = Field(None, description='Nginx配置文件路径')
    nginx_server_ip: str | None = Field(None, description='Nginx服务器IP')

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


class ProjectNameCheckResponseData(BaseModel):
    """项目名称检查结果数据。"""
    exists: bool = Field(False, description='项目目录是否已存在')
    target_dir: str = Field('', description='解析后的目标项目目录')


class ProjectNameCheckResponse(base.BaseResponse):
    """项目名称检查接口响应。"""
    data: ProjectNameCheckResponseData


class ProjectRealCreateRequest(BaseModel):
    """真实创建项目请求体。"""
    name: str = Field(..., description='项目名称')
    description: str = Field('', description='项目描述')
    python_version: str = Field(..., description='Python版本，例如3.10')
    base_path: str = Field(..., description='项目基础路径')
    conda_env_name: str = Field(..., description='Conda环境名称')
    use_database: bool = Field(False, description='是否配置数据库')
    database_name: str = Field('', description='数据库名称，可选')
    database_host: str = Field('', description='数据库主机，可选')
    database_port: int | None = Field(None, description='数据库端口，可选')
    database_user: str = Field('', description='数据库账号，可选')
    database_password: str = Field('', description='数据库密码，可选')
    use_nginx: bool = Field(False, description='是否启用Nginx配置')
    nginx_server_ip: str = Field('', description='Nginx服务器IP，可选')
    nginx_conf_path: str = Field('', description='已选择或新建的Nginx配置文件路径，可选')
    frontend_port: str = Field('', description='Nginx前端端口，可选')
    backend_deploy_port: str = Field('', description='Nginx proxy_pass使用的后端部署端口，可选')
    nginx_config_text: str = Field('', description='已确认的Nginx server块文本，可选')
    server_ip: str = Field(..., description='服务器IP')


class ProjectRealCreateResponseData(BaseModel):
    """真实创建项目结果数据。"""
    project_id: int = Field(..., description='创建出的项目ID')
    status: str = Field('创建成功', description='最终状态')
    backend_path: str = Field(..., description='后端代码路径')
    conda_env_name: str = Field(..., description='Conda环境名称')
    python_version: str = Field(..., description='Python版本')
    logs: List[str] = Field(default_factory=list, description='执行日志')


class ProjectRealCreateResponse(base.BaseResponse):
    """真实创建项目接口响应。"""
    data: ProjectRealCreateResponseData
