"""项目详情配置模块，集中维护详情展示、日志展示和字段样式相关常量。

用途：
- 避免项目详情服务中直接硬编码字段中文名、分组顺序和默认日志文案。
- 前端详情侧边栏、操作日志快照都依赖这些字段语义保持一致。
"""

from __future__ import annotations

from typing import Final

# 项目详情字段中文名，供详情侧边栏和日志变更列表共同使用。
PROJECT_DETAIL_FIELD_LABELS: Final[dict[str, str]] = {
    'id': '项目ID',
    'name': '项目名称',
    'description': '项目描述',
    'owner': '所属人员',
    'server_ip': '项目服务器IP',
    'backend_path': '后端代码位置',
    'frontend_path': '前端代码位置',
    'entry_file_path': '项目入口文件位置',
    'conda_env_name': 'Conda环境名称',
    'conda_env_path': 'Conda环境位置',
    'python_version': '项目记录Python版本',
    'conda_python_version': 'Conda中的Python版本',
    'database_name': '数据库名称',
    'database_host': '数据库IP',
    'database_port': '数据库端口',
    'database_user': '数据库账号',
    'database_password': '数据库密码',
    'nginx_server_ip': 'Nginx服务器IP',
    'nginx_conf_path': 'Nginx配置文件路径',
    'frontend_port': 'Nginx前端端口',
    'backend_dev_port': '后端开发端口',
    'backend_deploy_port': '后端部署端口',
    'nginx_config_text': 'Nginx详细配置',
    'dev_start_command': '开发启动命令',
    'deploy_start_command': '部署启动命令',
    'status': '项目状态',
    'auto_start': '是否开机自启',
    'remark': '备注',
    'created_at': '创建时间',
    'updated_at': '更新时间',
}

# 项目详情侧边栏分组顺序，列表顺序就是前端展示顺序。
PROJECT_DETAIL_GROUPS: Final[list[tuple[str, list[str]]]] = [
    ('基础信息', ['id', 'name', 'description', 'owner', 'server_ip', 'status', 'created_at', 'updated_at']),
    ('路径信息', ['backend_path', 'frontend_path', 'entry_file_path']),
    ('Conda环境', ['conda_env_name', 'conda_env_path', 'python_version', 'conda_python_version']),
    ('数据库配置', ['database_name', 'database_host', 'database_port', 'database_user', 'database_password']),
    ('Nginx配置', ['nginx_server_ip', 'nginx_conf_path', 'frontend_port', 'backend_deploy_port', 'nginx_config_text']),
    ('启动配置', ['backend_dev_port', 'dev_start_command', 'deploy_start_command', 'auto_start', 'remark']),
]

# 操作日志变更字段中文名，必要时覆盖详情字段中文名。
CHANGE_FIELD_LABELS: Final[dict[str, str]] = PROJECT_DETAIL_FIELD_LABELS | {
    'conda_env_name': 'Conda环境',
    'dev_start_command': '开发启动命令',
    'deploy_start_command': '部署启动命令',
}

# 即使为空也需要展示的详情字段，保证详情抽屉基础信息完整。
PROJECT_DETAIL_ALWAYS_SHOW_FIELDS: Final[set[str]] = {
    'id',
    'name',
    'owner',
    'server_ip',
    'status',
    'created_at',
    'updated_at',
}

# 需要按等宽字体展示的字段，主要用于路径、命令和配置文本。
PROJECT_DETAIL_MONO_FIELDS: Final[set[str]] = {
    'backend_path',
    'frontend_path',
    'entry_file_path',
    'nginx_conf_path',
    'nginx_config_text',
    'dev_start_command',
    'deploy_start_command',
    'conda_env_path',
}

# 需要按敏感字段处理的字段，前端可据此做遮罩或弱化展示。
PROJECT_DETAIL_SECRET_FIELDS: Final[set[str]] = {'database_password'}

# 没有历史操作日志时，后端自动生成快照日志使用的动作编码。
PROJECT_FALLBACK_LOG_ACTION: Final[str] = 'create_snapshot'

# 没有历史操作日志时，后端自动生成快照日志使用的动作名称。
PROJECT_FALLBACK_LOG_ACTION_LABEL: Final[str] = '创建项目'

# 没有历史操作日志时，快照日志的说明文案。
PROJECT_FALLBACK_LOG_DETAIL_MESSAGE: Final[str] = '该项目创建时操作日志功能尚未启用，当前展示的是根据项目表生成的创建快照。'

# 没有历史操作日志时，快照日志摘要的模板。
PROJECT_FALLBACK_LOG_SUMMARY_TEMPLATE: Final[str] = '项目创建记录：{project_name}'
