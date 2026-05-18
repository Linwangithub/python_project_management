import re

# 当前后端允许直接视为本机的 IP 集合；shell_utils 用它判断是本机执行还是走 SSH。
LOCAL_SERVER_IPS = {'127.0.0.1', 'localhost', '192.168.93.129'}

# Conda 初始化脚本；所有 conda create/run/remove 命令前都要先 source 这个脚本。
CONDA_INIT = 'source /root/miniforge3/etc/profile.d/conda.sh >/dev/null 2>&1 || true'

# 默认前端路径，当前创建项目时如果未启用 Nginx，则前端路径可以为空。
DEFAULT_FRONTEND_PATH = ''

# 默认开发启动命令模板；runtime_utils 在用户未配置命令时可作为兜底参考。
DEFAULT_DEV_CMD_TPL = 'python main.py'

# 默认部署启动命令模板；其中 {port} 会被 runtime_utils 替换为配置端口。
DEFAULT_DEPLOY_CMD_TPL = 'gunicorn main:app -b 0.0.0.0:{port}'

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

# 数据库名安全正则：数据库名会进入 SQL DDL，只允许字母、数字、下划线。
SAFE_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9_]+$')

# Conda 环境名安全正则：环境名会进入 shell 命令，只允许常见安全字符。
SAFE_ENV_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')

