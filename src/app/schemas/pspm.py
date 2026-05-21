from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

RoleName = Literal['root', 'user']
ProjectStatusName = Literal['运行中', '已停止', '创建中', '创建成功', '创建失败']
ProjectDeleteScope = Literal['project_only', 'project_and_conda', 'project_conda_and_db', 'project_conda_nginx', 'project_conda_db_nginx']


class UserItem(BaseModel):
    """用户管理列表中的单条用户数据。"""
    id: int = Field(..., description='User ID')
    userid: int = Field(..., description='Business user ID')
    username: str = Field(..., description='Username')
    password: str = Field(..., description='Password')
    role: RoleName = Field(..., description='Role')
    operator: str = Field('system', description='Operator')
    created_at: datetime | None = Field(None, description='Created at')


class UserItems(BaseModel):
    """用户管理分页数据。"""
    total: int = Field(0, description='Total')
    data: List[UserItem] = Field(default_factory=list, description='Rows')


class UserItemsResponse(base.BaseResponse):
    """用户管理列表接口响应。"""
    data: UserItems


class EnvItem(BaseModel):
    """环境管理列表中的单条环境数据。"""
    id: int = Field(..., description='Env ID')
    env_name: str = Field(..., description='Env name')
    project_name: str | None = Field(None, description='Linked project')
    python_version: str | None = Field(None, description='Python version')
    main_packages: str | None = Field(None, description='Main packages')
    created_at: datetime | None = Field(None, description='Created at')


class EnvItems(BaseModel):
    """环境管理分页数据。"""
    total: int = Field(0, description='Total')
    data: List[EnvItem] = Field(default_factory=list, description='Rows')


class EnvItemsResponse(base.BaseResponse):
    """环境管理列表接口响应。"""
    data: EnvItems


class ServerItem(BaseModel):
    """服务器管理列表中的单条服务器数据。"""
    id: int = Field(..., description='Server ID')
    alias: str | None = Field(None, description='Server alias')
    ip: str = Field(..., description='Server IP')
    root_password: str = Field('', description='Root password (plain text)')
    users: str = Field('root', description='Assigned users')
    remark: str | None = Field(None, description='Remark')


class ServerItems(BaseModel):
    """服务器管理分页数据。"""
    total: int = Field(0, description='Total')
    data: List[ServerItem] = Field(default_factory=list, description='Rows')


class ServerItemsResponse(base.BaseResponse):
    """服务器管理列表接口响应。"""
    data: ServerItems


class ProjectItem(BaseModel):
    """项目管理列表中的单条项目数据。"""
    id: int = Field(..., description='Project ID')
    owner_id: int = Field(..., description='Owner user ID')
    owner: str = Field(..., description='Owner username')

    name: str = Field(..., description='Project name')
    description: str | None = Field(None, description='Project description')

    server_id: int | None = Field(None, description='Server ID')
    server_ip: str | None = Field(None, description='Server IP')

    backend_path: str | None = Field(None, description='Backend path')
    frontend_path: str | None = Field(None, description='Frontend path')
    nginx_conf_path: str | None = Field(None, description='Nginx config path')
    nginx_server_ip: str | None = Field(None, description='Nginx server IP')
    nginx_config_text: str | None = Field(None, description='Nginx server block text')

    frontend_port: str | None = Field(None, description='Nginx frontend port')
    backend_dev_port: str | None = Field(None, description='Backend dev port')
    backend_deploy_port: str | None = Field(None, description='Backend deploy port')

    database_name: str | None = Field(None, description='Database name')
    database_host: str | None = Field(None, description='Database host')
    database_port: str | None = Field(None, description='Database port')
    database_user: str | None = Field(None, description='Database user')
    database_password: str | None = Field(None, description='Database password')
    conda_env_name: str | None = Field(None, description='Conda env name')
    python_version: str | None = Field(None, description='Python version')

    dev_start_command: str | None = Field(None, description='Dev start command')
    deploy_start_command: str | None = Field(None, description='Deploy start command')
    entry_file_path: str | None = Field(None, description='Entry file path')

    running_port: str | None = Field(None, description='Runtime detected port')
    service_status: str = Field('已停止', description='Service running status')
    project_status: str = Field('未检测', description='Project health status')
    project_status_detail: str | None = Field('', description='Project health detail')
    nginx_info: str | None = Field(None, description='Nginx summary')
    database_info: str | None = Field(None, description='Database summary')

    status: ProjectStatusName = Field('已停止', description='Status')
    created_at: datetime | None = Field(None, description='Created at')


class ProjectItems(BaseModel):
    """项目管理分页数据。"""
    total: int = Field(0, description='Total')
    data: List[ProjectItem] = Field(default_factory=list, description='Rows')


class ProjectItemsResponse(base.BaseResponse):
    """项目管理列表接口响应。"""
    data: ProjectItems


class ProjectHealthCheckResponse(base.BaseResponse):
    """单个项目健康检测接口响应。"""
    data: ProjectItem


class ProjectDetailField(BaseModel):
    """项目详情侧边栏中的一个字段。"""
    key: str = Field('', description='Field key')
    label: str = Field(..., description='Field label')
    value: str = Field('', description='Display value')
    mono: bool = Field(False, description='Whether to use monospace style')
    secret: bool = Field(False, description='Whether this field is sensitive')


class ProjectDetailSection(BaseModel):
    """项目详情侧边栏中的一个信息分组。"""
    title: str = Field(..., description='Section title')
    fields: List[ProjectDetailField] = Field(default_factory=list, description='Fields')


class ProjectDetailData(BaseModel):
    """项目完整详情数据。"""
    project_id: int = Field(..., description='Project ID')
    project_name: str = Field(..., description='Project name')
    sections: List[ProjectDetailSection] = Field(default_factory=list, description='Detail sections')


class ProjectDetailResponse(base.BaseResponse):
    """项目详情接口响应。"""
    data: ProjectDetailData


class ProjectOperationLogItem(BaseModel):
    """项目操作日志列表中的单条日志。"""
    id: int = Field(..., description='Log ID')
    project_id: int = Field(..., description='Project ID')
    operator_id: int | None = Field(None, description='Operator ID')
    operator_name: str = Field('', description='Operator username')
    action: str = Field('', description='Action code')
    action_label: str = Field('', description='Action label')
    summary: str = Field('', description='Summary')
    before_data: dict[str, Any] | None = Field(None, description='Before data')
    after_data: dict[str, Any] | None = Field(None, description='After data')
    detail: dict[str, Any] | None = Field(None, description='Detail data')
    created_at: datetime | None = Field(None, description='Created at')


class ProjectOperationLogsData(BaseModel):
    """项目操作日志分页数据。"""
    project_id: int = Field(..., description='Project ID')
    project_name: str = Field(..., description='Project name')
    total: int = Field(0, description='Total')
    data: List[ProjectOperationLogItem] = Field(default_factory=list, description='Rows')


class ProjectOperationLogsResponse(base.BaseResponse):
    """项目操作日志接口响应。"""
    data: ProjectOperationLogsData


class UserCreate(BaseModel):
    """创建用户请求体。"""
    username: str = Field(..., description='Username')
    password: str = Field(..., description='Password')
    role: RoleName = Field('user', description='Role')


class EnvCreate(BaseModel):
    """创建环境请求体。"""
    env_name: str = Field(..., description='Env name')
    project_name: str | None = Field(None, description='Linked project')
    python_version: str | None = Field(None, description='Python version')
    main_packages: str | None = Field(None, description='Main packages')


class EnvUpdate(BaseModel):
    """更新环境请求体。"""
    project_name: str | None = Field(None, description='Linked project')
    python_version: str | None = Field(None, description='Python version')
    main_packages: str | None = Field(None, description='Main packages')


class ServerCreate(BaseModel):
    """新增服务器请求体。"""
    alias: str | None = Field(None, description='Server alias')
    ip: str = Field(..., description='Server IP')
    root_password: str | None = Field(None, description='Root password (plain text)')
    remark: str | None = Field(None, description='Remark')


class ServerUserCreate(BaseModel):
    """服务器新增 Linux 用户请求体。"""
    server_id: int = Field(..., description='Server ID')
    username: str = Field(..., description='Linux username')


class ServerUserDelete(BaseModel):
    """服务器删除 Linux 用户请求体。"""
    server_id: int = Field(..., description='Server ID')
    username: str = Field(..., description='Linux username')


class ProjectCreate(BaseModel):
    """创建项目基础记录请求体。"""
    name: str = Field(..., description='Project name')
    description: str | None = Field(None, description='Project description')
    server_id: int | None = Field(None, description='Server ID')

    backend_path: str | None = Field(None, description='Backend path')
    frontend_path: str | None = Field(None, description='Frontend path')
    nginx_conf_path: str | None = Field(None, description='Nginx config path')
    nginx_server_ip: str | None = Field(None, description='Nginx server IP')

    frontend_port: str | None = Field(None, description='Nginx frontend port')
    backend_dev_port: str | None = Field(None, description='Backend dev port')
    backend_deploy_port: str | None = Field(None, description='Backend deploy port')

    database_name: str | None = Field(None, description='Database name')
    database_host: str | None = Field(None, description='Database host')
    database_port: str | None = Field(None, description='Database port')
    database_user: str | None = Field(None, description='Database user')
    database_password: str | None = Field(None, description='Database password')
    conda_env_name: str | None = Field(None, description='Conda env name')
    python_version: str | None = Field(None, description='Python version')

    dev_start_command: str | None = Field(None, description='Dev start command')
    deploy_start_command: str | None = Field(None, description='Deploy start command')
    entry_file_path: str | None = Field(None, description='Entry file path')


class ProjectNameCheckResponseData(BaseModel):
    """项目名称检查结果数据。"""
    exists: bool = Field(False, description='Whether project folder already exists')
    target_dir: str = Field('', description='Resolved target project directory')


class ProjectNameCheckResponse(base.BaseResponse):
    """项目名称检查接口响应。"""
    data: ProjectNameCheckResponseData


class ProjectRealCreateRequest(BaseModel):
    """真实创建项目请求体。"""
    name: str = Field(..., description='Project name')
    description: str = Field('', description='Project description')
    python_version: str = Field(..., description='Python version, e.g. 3.10')
    base_path: str = Field(..., description='Base path, e.g. /root/project')
    conda_env_name: str = Field(..., description='Conda env name')
    use_database: bool = Field(False, description='Whether to configure database')
    database_name: str = Field('', description='Database name (optional)')
    database_host: str = Field('', description='Database host (optional)')
    database_port: int | None = Field(None, description='Database port (optional)')
    database_user: str = Field('', description='Database user (optional)')
    database_password: str = Field('', description='Database password (optional)')
    use_nginx: bool = Field(False, description='Whether to enable nginx config')
    nginx_server_ip: str = Field('', description='Nginx server IP (optional)')
    nginx_conf_path: str = Field('', description='Selected or new Nginx config path (optional)')
    frontend_port: str = Field('', description='Nginx frontend port (optional)')
    backend_deploy_port: str = Field('', description='Backend deploy port used by nginx proxy_pass (optional)')
    nginx_config_text: str = Field('', description='Confirmed Nginx server block text (optional)')
    server_ip: str = Field(..., description='Server IP')


class ProjectRealCreateResponseData(BaseModel):
    """真实创建项目结果数据。"""
    project_id: int = Field(..., description='Created project ID')
    status: str = Field('创建成功', description='Final status')
    backend_path: str = Field(..., description='Backend path')
    conda_env_name: str = Field(..., description='Conda env name')
    python_version: str = Field(..., description='Python version')
    logs: List[str] = Field(default_factory=list, description='Execution logs')


class ProjectRealCreateResponse(base.BaseResponse):
    """真实创建项目接口响应。"""
    data: ProjectRealCreateResponseData


class ProjectDatabaseCheckRequest(BaseModel):
    """数据库连接和数据库名称检查请求体。"""
    host: str = Field(..., description='Database host')
    port: int = Field(..., description='Database port')
    username: str = Field(..., description='Database user')
    password: str = Field('', description='Database password')
    database_name: str = Field('', description='Database name to check')


class ProjectDatabaseCheckResponseData(BaseModel):
    """数据库检查结果数据。"""
    ok: bool = Field(False, description='Whether db connection is ok')
    message: str = Field('', description='Check result message')
    server_mysql_ok: bool = Field(False, description='Whether mysql on server is reachable')
    database_exists: bool = Field(False, description='Whether target database exists')
    can_create: bool = Field(False, description='Whether target database can be created')


class ProjectDatabaseCheckResponse(base.BaseResponse):
    """数据库检查接口响应。"""
    data: ProjectDatabaseCheckResponseData


class ProjectNginxCheckRequest(BaseModel):
    """Nginx 服务和配置文件检查请求体。"""
    server_ip: str = Field(..., description='Server IP')
    nginx_server_ip: str = Field('', description='Nginx server IP')


class ProjectNginxConfigFile(BaseModel):
    """可选 Nginx 配置文件数据。"""
    path: str = Field(..., description='Nginx config file path')
    source: str = Field('include', description='Config file source: main/top/http/include')
    include_pattern: str = Field('', description='Original include pattern')
    kind: str = Field('file', description='Item kind: file/include_pattern')
    selectable: bool = Field(True, description='Whether this item can be selected as nginx config file')
    status: str = Field('available', description='Frontend status: available/disabled')


class ProjectNginxNewConfDir(BaseModel):
    """可新建 Nginx 配置文件的目录数据。"""
    base_dir: str = Field(..., description='Fixed base dir up to nginx level')
    directory: str = Field(..., description='Allowed directory for new nginx conf file')
    folder_name: str = Field('', description='Folder name under base dir')
    include_pattern: str = Field('', description='Original include pattern')
    source: str = Field('', description='Include source: top/http')
    label: str = Field('', description='Display label')
    status: str = Field('available', description='Frontend status: available/disabled')


class ProjectNginxCheckResponseData(BaseModel):
    """Nginx 检查结果数据。"""
    ok: bool = Field(False, description='Whether nginx is available')
    running: bool = Field(False, description='Whether nginx service is running')
    conf_path: str = Field('', description='Running nginx config path')
    conf_files: List[ProjectNginxConfigFile] = Field(default_factory=list, description='Available nginx config files')
    new_conf_dirs: List[ProjectNginxNewConfDir] = Field(default_factory=list, description='Allowed dirs for new nginx config files')
    message: str = Field('', description='Check result message')


class ProjectNginxCheckResponse(base.BaseResponse):
    """Nginx 检查接口响应。"""
    data: ProjectNginxCheckResponseData


class ProjectPortCheckRequest(BaseModel):
    """端口检查请求体。"""
    project_id: int = Field(..., description='Project ID')
    port: int = Field(..., description='Port number')
    check_nginx_conf: bool = Field(False, description='Whether to check nginx listen conflict in config')
    nginx_server_ip: str = Field('', description='Nginx server IP (optional)')


class ProjectPortCheckResponseData(BaseModel):
    """端口检查结果数据。"""
    ok: bool = Field(False, description='Whether port is available')
    port: int = Field(..., description='Port number')
    range_ok: bool = Field(False, description='Whether port in valid range')
    in_use: bool = Field(False, description='Whether port is currently in use by process')
    nginx_conflict: bool = Field(False, description='Whether nginx config already contains this listen port')
    nginx_conf_path: str = Field('', description='Running nginx config path')
    message: str = Field('', description='Check result message')


class ProjectPortCheckResponse(base.BaseResponse):
    """端口检查接口响应。"""
    data: ProjectPortCheckResponseData


class ProjectSettingUpdate(BaseModel):
    """项目设置保存请求体。"""
    description: str | None = Field(None, description='Project description')
    conda_env_name: str | None = Field(None, description='Conda env name')
    python_version: str | None = Field(None, description='Python version')
    create_conda_env: bool | None = Field(False, description='Create new conda env after setting is confirmed')
    drop_original_conda_env: bool | None = Field(False, description='Drop original conda env after setting is confirmed')
    entry_file_path: str | None = Field(None, description='Entry file path')
    backend_dev_port: str | None = Field(None, description='Backend dev port')
    backend_deploy_port: str | None = Field(None, description='Backend deploy port')
    frontend_port: str | None = Field(None, description='Frontend port')
    dev_start_command: str | None = Field(None, description='Dev start command')
    deploy_start_command: str | None = Field(None, description='Deploy start command')
    nginx_enabled: bool | None = Field(None, description='Whether nginx is enabled for this project')
    nginx_server_ip: str | None = Field(None, description='Nginx server IP')
    nginx_conf_path: str | None = Field(None, description='Nginx config path')
    nginx_config_text: str | None = Field(None, description='Nginx server block text')
    drop_original_nginx_config: bool | None = Field(False, description='Drop original Nginx server block after setting is confirmed')
    database_name: str | None = Field(None, description='Database name')
    database_host: str | None = Field(None, description='Database host')
    database_port: str | None = Field(None, description='Database port')
    database_user: str | None = Field(None, description='Database user')
    database_password: str | None = Field(None, description='Database password')
    drop_original_database: bool | None = Field(False, description='Drop original project database after setting is confirmed')


class ProjectEntryPathNode(BaseModel):
    """项目入口文件选择器中的目录或文件节点。"""
    label: str = Field(..., description='Node label')
    value: str = Field(..., description='Relative path value')
    leaf: bool = Field(False, description='Whether this node is leaf file')


class ProjectEntryPathChildrenResponse(base.BaseResponse):
    """项目入口文件子节点接口响应。"""
    data: List[ProjectEntryPathNode] = Field(default_factory=list, description='Children nodes')


class ProjectCondaEnvListData(BaseModel):
    """Conda 环境列表数据。"""
    envs_dir: str = Field('', description='Conda envs directory')
    envs: List[str] = Field(default_factory=list, description='Conda env names')


class ProjectCondaEnvListResponse(base.BaseResponse):
    """Conda 环境列表接口响应。"""
    data: ProjectCondaEnvListData = Field(default_factory=ProjectCondaEnvListData, description='Conda env list data')


class ProjectSyncPathChildrenRequest(BaseModel):
    """同步已有项目时查询项目目录子项的请求体。"""
    server_ip: str = Field(..., description='Server IP')
    rel_path: str = Field('', description='Relative path under configured project base path')


class ProjectSyncPathNode(BaseModel):
    """同步已有项目目录选择器节点。"""
    label: str = Field(..., description='Display label')
    value: str = Field(..., description='Relative path value')
    abs_path: str = Field(..., description='Absolute path on server')
    leaf: bool = Field(False, description='Whether node is leaf')


class ProjectSyncPathChildrenResponse(base.BaseResponse):
    """同步已有项目目录子项接口响应。"""
    data: List[ProjectSyncPathNode] = Field(default_factory=list, description='Directory nodes')


class ProjectSyncEntryPathChildrenRequest(BaseModel):
    """同步已有项目时查询入口文件子项的请求体。"""
    server_ip: str = Field(..., description='Server IP')
    backend_path: str = Field(..., description='Existing project directory')
    rel_path: str = Field('', description='Relative path under selected project directory')


class ProjectSyncEntryPathChildrenResponse(base.BaseResponse):
    """同步已有项目入口文件子项接口响应。"""
    data: List[ProjectEntryPathNode] = Field(default_factory=list, description='Entry file nodes')


class ProjectSyncCondaEnvListRequest(BaseModel):
    """同步已有项目时查询服务器 Conda 环境列表的请求体。"""
    server_ip: str = Field(..., description='Server IP')


class ProjectSyncCondaEnvListData(BaseModel):
    """同步已有项目 Conda 环境列表数据。"""
    envs_dir: str = Field('', description='Conda envs directory')
    envs: List[str] = Field(default_factory=list, description='Conda env names')


class ProjectSyncCondaEnvListResponse(base.BaseResponse):
    """同步已有项目 Conda 环境列表接口响应。"""
    data: ProjectSyncCondaEnvListData = Field(default_factory=ProjectSyncCondaEnvListData, description='Conda env list data')


class ProjectSyncCondaCheckRequest(BaseModel):
    """同步已有项目时检查 Conda 环境的请求体。"""
    server_ip: str = Field(..., description='Server IP')
    conda_env_name: str = Field(..., description='Conda env name')


class ProjectSyncCondaCheckData(BaseModel):
    """同步已有项目 Conda 检查结果。"""
    ok: bool = Field(False, description='Whether conda env exists and python version is detected')
    env_name: str = Field('', description='Conda env name')
    env_path: str = Field('', description='Conda env path')
    python_version: str = Field('', description='Actual Python version')
    message: str = Field('', description='Check message')


class ProjectSyncCondaCheckResponse(base.BaseResponse):
    """同步已有项目 Conda 检查接口响应。"""
    data: ProjectSyncCondaCheckData


class ProjectSyncDatabaseCheckRequest(BaseModel):
    """同步已有项目时检查数据库连接的请求体。"""
    host: str = Field(..., description='Database host')
    port: int = Field(..., description='Database port')
    username: str = Field(..., description='Database user')
    password: str = Field('', description='Database password')
    database_name: str = Field('', description='Optional database name that must already exist')


class ProjectSyncDatabaseCheckData(ProjectDatabaseCheckResponseData):
    """同步已有项目数据库连接检查结果。"""
    databases: list[str] = Field(default_factory=list, description='Visible database names')


class ProjectSyncDatabaseCheckResponse(base.BaseResponse):
    """同步已有项目数据库检查接口响应。"""
    data: ProjectSyncDatabaseCheckData


class ProjectSyncNginxServerBlockCheckRequest(BaseModel):
    """同步已有项目时检查 Nginx server 块是否匹配的请求体。"""
    server_ip: str = Field(..., description='Project server IP')
    nginx_server_ip: str = Field('', description='Nginx server IP')
    nginx_conf_path: str = Field(..., description='Existing nginx config file path')
    frontend_port: str = Field(..., description='Nginx frontend listen port')
    backend_deploy_port: str = Field(..., description='Backend deploy port in proxy_pass')


class ProjectSyncNginxServerBlockCheckData(BaseModel):
    """同步已有项目 Nginx server 块检查结果。"""
    ok: bool = Field(False, description='Whether matched server block exists')
    nginx_config_text: str = Field('', description='Matched server block text')
    message: str = Field('', description='Check message')


class ProjectSyncNginxServerBlockCheckResponse(base.BaseResponse):
    """同步已有项目 Nginx server 块检查接口响应。"""
    data: ProjectSyncNginxServerBlockCheckData


class ProjectSyncNginxServerPortOptionsRequest(BaseModel):
    """同步已有项目时查询已有 Nginx server 端口选项的请求体。"""
    server_ip: str = Field(..., description='Project server IP')
    nginx_server_ip: str = Field('', description='Nginx server IP')
    nginx_conf_path: str = Field(..., description='Existing nginx config file path')


class ProjectSyncNginxServerPortOption(BaseModel):
    """同步已有项目可选择的一组 Nginx 前端端口和后端代理端口。"""
    label: str = Field('', description='Display label')
    frontend_port: str = Field('', description='Nginx listen port')
    backend_deploy_port: str = Field('', description='proxy_pass backend port')
    server_name: str = Field('', description='server_name value')
    nginx_config_text: str = Field('', description='Matched server block text')


class ProjectSyncNginxServerPortOptionsData(BaseModel):
    """同步已有项目 Nginx server 端口选项数据。"""
    options: List[ProjectSyncNginxServerPortOption] = Field(default_factory=list, description='Port option list')


class ProjectSyncNginxServerPortOptionsResponse(base.BaseResponse):
    """同步已有项目 Nginx server 端口选项接口响应。"""
    data: ProjectSyncNginxServerPortOptionsData = Field(default_factory=ProjectSyncNginxServerPortOptionsData)


class ProjectSyncRequest(BaseModel):
    """同步已有项目最终提交请求体。"""
    server_ip: str = Field(..., description='Project server IP')
    name: str = Field(..., description='Project name')
    description: str = Field('', description='Project description')
    backend_path: str = Field(..., description='Existing project directory')
    entry_file_path: str = Field('', description='Entry file absolute path')
    conda_env_name: str = Field(..., description='Existing Conda env name')
    python_version: str = Field('', description='Detected Python version')
    use_database: bool = Field(False, description='Whether to bind existing database')
    database_name: str = Field('', description='Existing database name')
    database_host: str = Field('', description='Database host')
    database_port: int | None = Field(None, description='Database port')
    database_user: str = Field('', description='Database user')
    database_password: str = Field('', description='Database password')
    use_nginx: bool = Field(False, description='Whether to bind existing nginx config')
    nginx_server_ip: str = Field('', description='Nginx server IP')
    nginx_conf_path: str = Field('', description='Existing nginx config file path')
    frontend_port: str = Field('', description='Nginx frontend port')
    backend_deploy_port: str = Field('', description='Backend deploy port in proxy_pass')
    nginx_config_text: str = Field('', description='Nginx server block text')


class ProjectSyncResponseData(BaseModel):
    """同步已有项目结果数据。"""
    project_id: int = Field(..., description='Project ID')
    status: str = Field('同步成功', description='Sync status')
    backend_path: str = Field(..., description='Existing project directory')
    conda_env_name: str = Field(..., description='Conda env name')
    python_version: str = Field('', description='Python version')


class ProjectSyncResponse(base.BaseResponse):
    """同步已有项目接口响应。"""
    data: ProjectSyncResponseData


class ProjectCopyRequest(BaseModel):
    """复制项目请求体。"""
    target_server_ip: str = Field(..., description='Target server IP')
    target_dir: str = Field(..., description='Target directory')


class ProjectExportRequest(BaseModel):
    """导出项目请求体。"""
    target_dir: str = Field(..., description='Export directory')


class TerminalServerOption(BaseModel):
    """终端可连接服务器选项。"""
    server_id: int = Field(..., description='Server ID')
    ip: str = Field(..., description='Server IP')
    alias: str | None = Field(None, description='Server alias')
    ssh_port: int = Field(22, description='SSH port')


class TerminalServerOptionsResponse(base.BaseResponse):
    """终端可连接服务器列表接口响应。"""
    data: List[TerminalServerOption] = Field(default_factory=list, description='Terminal server options')


class TerminalSessionCreate(BaseModel):
    """创建终端会话请求体。"""
    server_ip: str = Field(..., description='Server IP')
    alias: str = Field(..., description='Session alias')


class TerminalSessionInfo(BaseModel):
    """终端会话信息。"""
    session_id: str = Field(..., description='Session ID')
    server_ip: str = Field(..., description='Server IP')
    alias: str = Field(..., description='Session alias')
    cwd: str = Field(..., description='Current working directory')
    prompt: str = Field(..., description='Prompt text')
    welcome_message: str = Field('连接成功！', description='Welcome message')


class TerminalSessionCreateResponse(base.BaseResponse):
    """创建终端会话接口响应。"""
    data: TerminalSessionInfo


class TerminalSessionClose(BaseModel):
    """关闭终端会话请求体。"""
    session_id: str = Field(..., description='Session ID')


class TerminalExecuteRequest(BaseModel):
    """执行终端命令请求体。"""
    session_id: str = Field(..., description='Session ID')
    command: str = Field(..., description='Command')
    mode: str | None = Field('', description='Execution mode')



class ProjectForegroundFinalize(BaseModel):
    """前台启动完成确认请求体。"""
    project_id: int = Field(..., description='Project ID')
    pid: str = Field(..., description='Started process PID')
    port: str | None = Field('', description='Detected or configured port')
    log_file: str | None = Field('', description='Current startup log file')


class TerminalCompleteRequest(BaseModel):
    """终端命令自动补全请求体。"""
    session_id: str = Field(..., description='Session ID')
    command: str = Field(..., description='Command input before Tab')


class TerminalCompleteResult(BaseModel):
    """终端命令自动补全结果数据。"""
    session_id: str = Field(..., description='Session ID')
    original_command: str = Field(..., description='Original command')
    completed_command: str = Field(..., description='Completed command')
    candidates: List[str] = Field(default_factory=list, description='Completion candidates')
    cwd: str = Field(..., description='Current working directory')
    message: str = Field('ok', description='Message')


class TerminalCompleteResponse(base.BaseResponse):
    """终端命令自动补全接口响应。"""
    data: TerminalCompleteResult


class TerminalExecuteResult(BaseModel):
    """终端命令执行结果数据。"""
    session_id: str = Field(..., description='Session ID')
    command: str = Field(..., description='Command')
    cwd: str = Field(..., description='Current working directory')
    prompt_before: str = Field(..., description='Prompt before execution')
    prompt_after: str = Field(..., description='Prompt after execution')
    exit_code: int = Field(0, description='Exit code')
    stdout: str = Field('', description='Standard output')
    stderr: str = Field('', description='Standard error')
    blocked: bool = Field(False, description='Whether blocked by policy')
    message: str = Field('ok', description='Message')


class TerminalExecuteResponse(base.BaseResponse):
    """终端命令执行接口响应。"""
    data: TerminalExecuteResult
