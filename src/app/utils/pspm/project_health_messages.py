"""项目健康检测文案配置模块。

用途：
- 集中维护项目检测状态、问题描述、列表汇总前缀等固定文案。
- project_health.py 只保留检测流程，文案调整统一修改本模块。
"""

from __future__ import annotations

from typing import Final

HEALTH_EMPTY_TEXT: Final[str] = '未配置'
HEALTH_STATUS_NORMAL: Final[str] = '正常'
HEALTH_STATUS_ERROR: Final[str] = '异常'
HEALTH_STATUS_UNCHECKED: Final[str] = '未检测'
HEALTH_SERVICE_RUNNING: Final[str] = '运行中'
HEALTH_SERVICE_STOPPED: Final[str] = '已停止'
HEALTH_DETAIL_SEPARATOR: Final[str] = '；'
HEALTH_SUMMARY_SEPARATOR: Final[str] = ' / '
HEALTH_NGINX_FRONTEND_PREFIX: Final[str] = '前端'
HEALTH_NGINX_BACKEND_PREFIX: Final[str] = '后端'
HEALTH_DATABASE_NAME_PREFIX: Final[str] = '库'

HEALTH_PROBLEM_MESSAGES: Final[dict[str, str]] = {
    'project_server_unavailable': '项目服务器不可用或无权限',
    'project_dir_missing': '项目目录不存在',
    'conda_missing': 'Conda环境不存在',
    'database_connect_failed': '数据库连接失败',
    'database_missing': '数据库不存在',
    'database_check_failed': '数据库检测失败',
    'nginx_server_unavailable': 'Nginx服务器不可用或无权限',
    'nginx_not_running': 'Nginx服务未运行',
    'nginx_config_mismatch': 'Nginx配置不匹配或文件不存在',
}


def health_problem(key: str) -> str:
    """读取项目健康检测问题文案。

    参数：
    - key：HEALTH_PROBLEM_MESSAGES 中的问题键。

    返回：
    - str：中文问题文案。
    """
    return HEALTH_PROBLEM_MESSAGES.get(key, key)


def health_pair(label: str, value: str) -> str:
    """渲染项目列表汇总中的 label:value 文案。

    参数：
    - label：中文标签，例如“前端”。
    - value：字段值。

    返回：
    - str：形如“前端:8080”的汇总文本。
    """
    return f'{label}:{value}' if value else ''
