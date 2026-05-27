"""项目管理配置模块，集中维护默认路径、端口范围、删除范围和安全正则。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import re


# 后端 API 默认监听地址；main.py 命令行未传 host 时使用。
DEFAULT_API_HOST = '0.0.0.0'

# 后端 API 默认监听端口；main.py 命令行未传 port 时使用。
DEFAULT_API_PORT = 8888

# 项目服务默认绑定地址；生成 gunicorn/uvicorn 启动命令时使用。
SERVICE_BIND_HOST = '0.0.0.0'

# TCP/UDP 网络端口通用范围。
NETWORK_PORT_MIN = 1
NETWORK_PORT_MAX = 65535

# 终端默认 home 目录；WebSocket 终端会话未指定目录时使用。
TERMINAL_HOME_DIR = '/root'

# 终端默认主机展示名；无法解析真实 hostname 时使用。
TERMINAL_DEFAULT_HOST_LABEL = 'wcp'

# 终端普通命令默认超时时间，单位：秒。
TERMINAL_COMMAND_TIMEOUT_SECONDS = 30

# 终端下载凭证有效期，单位：秒。
TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS = 300

# WebSocket 终端历史输出缓冲行数上限。
TERMINAL_WS_OUTPUT_BUFFER_LIMIT = 800

# 当前后端允许直接视为本机的 IP 集合；shell_utils 用它判断是本机执行还是走 SSH。
LOCAL_SERVER_IPS = {'127.0.0.1', 'localhost', '192.168.93.129'}

# Conda 初始化脚本；所有 conda create/run/remove 命令前都要先 source 这个脚本。
CONDA_INIT = 'source /root/miniforge3/etc/profile.d/conda.sh >/dev/null 2>&1 || true'


# Conda 初始化脚本候选路径；运行时会按顺序探测实际存在的脚本。
CONDA_INIT_CANDIDATE_PATHS = [
  '/root/miniforge3/etc/profile.d/conda.sh',
  '/root/miniconda3/etc/profile.d/conda.sh',
  '/root/anaconda3/etc/profile.d/conda.sh',
  '/opt/miniforge3/etc/profile.d/conda.sh',
  '/opt/miniconda3/etc/profile.d/conda.sh',
  '/opt/anaconda3/etc/profile.d/conda.sh',
  '/home/wcp/project_data/miniforge3/etc/profile.d/conda.sh',
]

# 公共前端打包资源根目录；Nginx root 默认使用该目录，避免访问 /root 权限受限。
FRONTEND_DIST_BASE_DIR = '/data/frontend_dist'

# root 用户项目目录前缀。
ROOT_PROJECT_BASE_DIR = '/root/project'

# root 用户同步已有项目时的起始浏览目录；root 可从系统根目录逐层选择已有项目。
ROOT_SYNC_BASE_DIR = '/'

# 普通用户项目目录模板；{username} 会在接口返回用户信息时替换为真实用户名。
USER_PROJECT_BASE_PATH_TEMPLATE = '/home/{username}/project'

# 普通用户 home 目录模板；同步已有项目时作为用户目录起点。
USER_HOME_BASE_PATH_TEMPLATE = '/home/{username}'

# 禁止被删除的高危项目路径集合；删除项目目录前必须命中安全校验。
FORBIDDEN_PROJECT_DELETE_PATHS = {'/', '/root', '/home', ROOT_PROJECT_BASE_DIR}

# 项目运行态文件根目录；保存 PID、meta、运行日志等运行期数据。
PROJECT_RUNTIME_BASE_DIR = '/tmp/pspm/runtime'

# 终端前台运行临时日志文件模板。
TERMINAL_FOREGROUND_LOG_TEMPLATE = '/tmp/pspm_terminal_fg_XXXXXX.log'

# WebSocket 终端临时 askpass 脚本模板。
TERMINAL_ASKPASS_TEMPLATE = '/tmp/pspm_ws_askpass_XXXXXX'

# 普通 SSH 命令临时 askpass 脚本模板。
SSH_ASKPASS_TEMPLATE = '/tmp/pspm_askpass_XXXXXX'

# 数据库默认主机；历史数据缺少 database_host 时使用。
DEFAULT_MYSQL_HOST = 'localhost'

# 数据库默认端口；项目状态检测等场景在历史数据缺少端口时使用。
DEFAULT_MYSQL_PORT = 3306

# 默认前端路径，当前创建项目时如果未启用 Nginx，则前端路径可以为空。
DEFAULT_FRONTEND_PATH = ''

# 默认开发启动命令模板；runtime_utils 在用户未配置命令时可作为兜底参考。
DEFAULT_DEV_CMD_TPL = 'python main.py'

# 默认部署启动命令模板；其中 {port} 会被 runtime_utils 替换为配置端口。
DEFAULT_DEPLOY_CMD_TPL = f'gunicorn main:app -b {SERVICE_BIND_HOST}:{{port}}'

# 默认入口文件；前端设置弹框可以基于该值提示用户，但项目表默认仍保存空字符串。
DEFAULT_ENTRY_FILE = 'main.py'

# 项目服务端口允许范围：避开 0-1023 系统保留端口和 49152 以上临时端口。
PORT_MIN = 1024
PORT_MAX = 49151

# 删除范围：只删除项目目录和项目记录。
DELETE_SCOPE_PROJECT_ONLY = 'project_only'

# 删除范围：删除项目目录、项目记录和对应 Conda 环境。
DELETE_SCOPE_PROJECT_AND_CONDA = 'project_and_conda'

# 删除范围：删除项目目录、项目记录、Conda 环境和数据库。
DELETE_SCOPE_PROJECT_CONDA_AND_DB = 'project_conda_and_db'

# 删除范围：删除项目目录、项目记录、Conda 环境和 Nginx 配置，不删除数据库。
DELETE_SCOPE_PROJECT_CONDA_NGINX = 'project_conda_nginx'

# 删除范围：删除项目目录、项目记录、Conda 环境、数据库和 Nginx 配置块。
DELETE_SCOPE_PROJECT_CONDA_DB_NGINX = 'project_conda_db_nginx'

# 删除范围白名单；接口层和 service 层用它拦截非法 delete_scope。
DELETE_SCOPE_OPTIONS = {
  DELETE_SCOPE_PROJECT_ONLY,
  DELETE_SCOPE_PROJECT_AND_CONDA,
  DELETE_SCOPE_PROJECT_CONDA_AND_DB,
  DELETE_SCOPE_PROJECT_CONDA_NGINX,
  DELETE_SCOPE_PROJECT_CONDA_DB_NGINX,
}

# 数据库名安全正则：数据库名会进入 SQL DDL，允许字母、数字、下划线和短横线。
SAFE_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

# Conda 环境名安全正则：环境名会进入 shell 命令，只允许常见安全字符。
SAFE_ENV_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')

