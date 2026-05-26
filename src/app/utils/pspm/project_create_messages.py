"""项目创建固定文案配置模块。

用途：
- 集中维护创建项目流程中的错误提示、动作日志、回滚日志和操作日志标题。
- 避免 service 层散落固定字符串，后续调整文案时只需要修改本模块。
"""

from __future__ import annotations

from typing import Final

PROJECT_NAME_EXISTS_MESSAGE: Final[str] = '项目名称已存在'
PROJECT_SERVER_PERMISSION_DENIED_MESSAGE: Final[str] = '当前用户无该服务器使用权限'
NGINX_SERVER_PERMISSION_DENIED_MESSAGE: Final[str] = '当前用户无该Nginx服务器使用权限'
NGINX_SERVICE_NOT_RUNNING_MESSAGE: Final[str] = 'nginx服务未开启'
NGINX_PORT_REQUIRED_MESSAGE: Final[str] = '启用nginx时必须填写前端端口和后端部署端口'
NGINX_PORT_SAME_MESSAGE: Final[str] = 'Nginx前端端口和后端部署端口不能相同'
PROJECT_CREATE_SUCCESS_STATUS: Final[str] = '创建成功'
PROJECT_CREATE_LOG_ACTION: Final[str] = 'create'
PROJECT_CREATE_LOG_ACTION_LABEL: Final[str] = '创建项目'
UNKNOWN_ERROR_MESSAGE: Final[str] = '未知错误'

PROJECT_CREATE_ACTION_TEMPLATES: Final[dict[str, str]] = {
    'nginx_unreachable': 'Nginx服务器不可达：{message}',
    'directory_exists': '目录已存在：{path}',
    'conda_query_failed': '查询Conda环境失败：{message}',
    'conda_exists': 'Conda环境已存在：{name}',
    'database_exists': '数据库 {name} 已存在，创建失败',
    'frontend_port_system_used': 'Nginx前端端口 {port} 已被系统占用',
    'backend_port_system_used': '后端部署端口 {port} 已被系统占用',
    'frontend_port_listen_used': 'Nginx前端端口 {port} 已在Nginx listen配置中占用',
    'frontend_port_proxy_used': 'Nginx前端端口 {port} 已在Nginx proxy_pass配置中占用',
    'backend_port_listen_used': '后端部署端口 {port} 已在Nginx listen配置中占用',
    'backend_port_proxy_used': '后端部署端口 {port} 已在Nginx proxy_pass配置中占用',
    'rollback_project_record_missing': '回滚项目记录失败：记录不存在（id={project_id}）',
    'rollback_project_record_deleted': '回滚：项目记录已删除（id={project_id}）',
    'rollback_project_record_failed': '回滚项目记录失败：{message}',
    'rollback_database_deleted': '回滚：数据库 {database_name} 已删除',
    'rollback_database_failed': '回滚数据库失败：{message}',
    'rollback_nginx_deleted': '回滚：Nginx配置已删除 {project_name}',
    'rollback_nginx_failed': '回滚Nginx配置失败：{message}',
    'rollback_conda_failed': '回滚Conda环境失败：{message}',
    'rollback_conda_deleted': '回滚：Conda环境 {conda_name} 已删除',
    'frontend_dist_kept': '前端打包基础目录保留：{path}',
    'rollback_project_dir_failed': '回滚项目目录失败：{message}',
    'rollback_project_dir_deleted': '回滚：项目目录 {path} 已删除',
    'create_project_dir_failed_action': '创建项目目录失败：{path}',
    'create_project_dir_failed': '创建项目目录失败：{message}',
    'create_project_dir_success': '创建项目目录成功：{path}',
    'create_frontend_dir_failed_action': '创建前端打包目录失败：{path}',
    'create_frontend_dir_failed': '创建前端打包目录失败：{path} {message}',
    'create_frontend_dir_success': '创建前端打包目录成功：{path}',
    'create_conda_start': '开始创建Conda环境：{conda_name}，Python版本：{python_version}',
    'create_conda_failed_action': '创建Conda环境失败：{conda_name}，Python版本：{python_version}',
    'create_conda_failed': '创建Conda环境失败：{message}',
    'create_conda_success': '创建Conda环境成功：{conda_name}，Python版本：{python_version}',
    'check_python_start': '检查Conda环境Python版本：{conda_name}',
    'check_python_failed_action': '检查Python版本失败：{conda_name}',
    'check_python_failed': 'Python版本验证失败：{message}',
    'check_python_success': '检查Python版本成功：{python_text}',
    'create_database_failed_mysql_action': '创建数据库失败：{database_name}，MySQL不可用',
    'create_database_failed_mysql': '创建数据库失败，MySQL不可用：{message}',
    'create_database_failed_action': '创建数据库失败：{database_name}',
    'create_database_failed': '创建数据库失败：{message}',
    'create_database_success': '创建数据库成功：{database_name}（{host}:{port}）',
    'write_nginx_failed_action': '写入Nginx配置失败：{path}',
    'create_nginx_failed': '创建Nginx配置失败：{message}',
    'write_nginx_success': '写入Nginx配置成功：{path}，listen={frontend_port}，proxy_pass={backend_port}',
    'create_project_record_success': '创建项目记录成功：{project_name}',
    'create_project_summary': '创建项目：{project_name}',
    'rollback_failed_suffix': '{message}；回滚异常：{rollback_errors}',
    'create_project_failed': '创建项目失败：{message}',
}


def render_project_create_message(key: str, **kwargs) -> str:
    """渲染创建项目流程固定文案。

    参数：
    - key：PROJECT_CREATE_ACTION_TEMPLATES 中的模板键。
    - kwargs：模板占位符需要的变量。

    返回：
    - str：渲染后的中文文案；找不到模板时返回 key 本身，便于排查配置遗漏。
    """
    template = PROJECT_CREATE_ACTION_TEMPLATES.get(key, key)
    return template.format(**kwargs)
