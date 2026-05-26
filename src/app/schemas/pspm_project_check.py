"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

class ProjectDatabaseCheckRequest(BaseModel):
    """数据库连接和数据库名称检查请求体。"""
    host: str = Field(..., description='数据库主机')
    port: int = Field(..., description='数据库端口')
    username: str = Field(..., description='数据库账号')
    password: str = Field('', description='数据库密码')
    database_name: str = Field('', description='需要检查的数据库名称')


class ProjectDatabaseCheckResponseData(BaseModel):
    """数据库检查结果数据。"""
    ok: bool = Field(False, description='数据库连接是否正常')
    message: str = Field('', description='检查结果消息')
    server_mysql_ok: bool = Field(False, description='服务器MySQL是否可连接')
    database_exists: bool = Field(False, description='目标数据库是否存在')
    can_create: bool = Field(False, description='目标数据库是否可创建')


class ProjectDatabaseCheckResponse(base.BaseResponse):
    """数据库检查接口响应。"""
    data: ProjectDatabaseCheckResponseData


class ProjectNginxCheckRequest(BaseModel):
    """Nginx 服务和配置文件检查请求体。"""
    server_ip: str = Field(..., description='服务器IP')
    nginx_server_ip: str = Field('', description='Nginx服务器IP')


class ProjectNginxConfigFile(BaseModel):
    """可选 Nginx 配置文件数据。"""
    path: str = Field(..., description='Nginx配置文件路径')
    source: str = Field('include', description='配置文件来源：主配置、顶层、http块或include')
    include_pattern: str = Field('', description='原始include匹配表达式')
    kind: str = Field('file', description='项目类型：文件或include表达式')
    selectable: bool = Field(True, description='该项是否可作为Nginx配置文件选择')
    status: str = Field('available', description='前端状态：可用或禁用')


class ProjectNginxNewConfDir(BaseModel):
    """可新建 Nginx 配置文件的目录数据。"""
    base_dir: str = Field(..., description='固定到Nginx层级的基础目录')
    directory: str = Field(..., description='允许新建Nginx配置文件的目录')
    folder_name: str = Field('', description='基础目录下的文件夹名称')
    include_pattern: str = Field('', description='原始include匹配表达式')
    source: str = Field('', description='include来源：顶层或http块')
    label: str = Field('', description='展示标题')
    status: str = Field('available', description='前端状态：可用或禁用')


class ProjectNginxCheckResponseData(BaseModel):
    """Nginx 检查结果数据。"""
    ok: bool = Field(False, description='Nginx是否可用')
    running: bool = Field(False, description='Nginx服务是否运行中')
    conf_path: str = Field('', description='正在使用的Nginx配置路径')
    conf_files: List[ProjectNginxConfigFile] = Field(default_factory=list, description='可用Nginx配置文件列表')
    new_conf_dirs: List[ProjectNginxNewConfDir] = Field(default_factory=list, description='允许新建Nginx配置文件的目录列表')
    message: str = Field('', description='检查结果消息')


class ProjectNginxCheckResponse(base.BaseResponse):
    """Nginx 检查接口响应。"""
    data: ProjectNginxCheckResponseData


class ProjectPortCheckRequest(BaseModel):
    """端口检查请求体。"""
    project_id: int = Field(..., description='项目ID')
    port: int = Field(..., description='端口号')
    check_nginx_conf: bool = Field(False, description='是否检查Nginx listen配置冲突')
    nginx_server_ip: str = Field('', description='Nginx服务器IP，可选')


class ProjectPortCheckResponseData(BaseModel):
    """端口检查结果数据。"""
    ok: bool = Field(False, description='端口是否可用')
    port: int = Field(..., description='端口号')
    range_ok: bool = Field(False, description='端口是否在有效范围内')
    in_use: bool = Field(False, description='端口是否被进程占用')
    nginx_conflict: bool = Field(False, description='Nginx配置是否已包含该listen端口')
    nginx_conf_path: str = Field('', description='正在使用的Nginx配置路径')
    message: str = Field('', description='检查结果消息')


class ProjectPortCheckResponse(base.BaseResponse):
    """端口检查接口响应。"""
    data: ProjectPortCheckResponseData
