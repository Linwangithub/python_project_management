"""项目管理 CRUD 模块，封装项目、服务器、环境和日志的数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

from app.crud.pspm_env import CRUDPspmEnv, envs
from app.crud.pspm_helpers import (
    add_assigned_user,
    get_server_ip_map,
    get_user_name_map,
    is_root_user,
    is_valid_linux_username,
    normalize_assigned_users,
    project_status_to_name,
    remove_assigned_user,
    role_keys_to_name,
)
from app.crud.pspm_project import CRUDPspmProject, projects
from app.crud.pspm_server import CRUDPspmServer, servers

__all__ = [
    'CRUDPspmEnv',
    'CRUDPspmProject',
    'CRUDPspmServer',
    'add_assigned_user',
    'envs',
    'get_server_ip_map',
    'get_user_name_map',
    'is_root_user',
    'is_valid_linux_username',
    'normalize_assigned_users',
    'project_status_to_name',
    'projects',
    'remove_assigned_user',
    'role_keys_to_name',
    'servers',
]
