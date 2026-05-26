"""项目创建请求归一化和 Conda 环境解析工具。

本模块集中维护真实创建项目请求中的基础字段清洗、数据库配置归一化、
以及 Conda 环境列表解析和重复检测，减少 project_create.py 主流程中的细节代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException

from app import schemas
from app.utils.pspm.db_utils import _safe_db_host, _safe_db_identifier, _safe_db_port, _safe_db_user, _safe_optional_db_name
from app.utils.pspm.path_utils import _normalize_path, _safe_conda_name, _safe_project_name, _safe_python_version


@dataclass
class ProjectCreateNormalizedInput:
  """真实创建项目请求归一化后的基础输入。"""

  project_name: str
  python_version: str
  conda_name: str
  use_database: bool
  database_name: str
  db_host: str
  db_port: int | None
  db_user: str
  db_password: str
  base_path: str
  use_nginx: bool
  server_ip: str
  nginx_server_ip: str
  requested_nginx_conf_path: str
  confirmed_nginx_config_text: str


def normalize_project_create_input(payload: schemas.pspm.ProjectRealCreateRequest) -> ProjectCreateNormalizedInput:
  """归一化真实创建项目请求体。

  参数：
  - payload：新建项目弹框提交的完整请求体。

  返回：
  - ProjectCreateNormalizedInput：已经完成基础清洗和安全校验的字段集合。
  """
  project_name = _safe_project_name(payload.name)
  python_version = _safe_python_version(payload.python_version)
  conda_name = _safe_conda_name(payload.conda_env_name)
  use_database = bool(payload.use_database)
  database_name_input = _safe_optional_db_name(payload.database_name)
  db_host = (payload.database_host or '').strip()
  db_port = payload.database_port
  db_user = (payload.database_user or '').strip()
  db_password = str(payload.database_password or '')
  base_path = _normalize_path(payload.base_path)
  use_nginx = bool(payload.use_nginx)
  server_ip = (payload.server_ip or '').strip()
  nginx_server_ip = (payload.nginx_server_ip or server_ip).strip()
  requested_nginx_conf_path = str(payload.nginx_conf_path or '').strip()
  confirmed_nginx_config_text = str(payload.nginx_config_text or '').strip()

  if use_database:
    database_name = _safe_db_identifier(database_name_input or project_name)
    db_host = _safe_db_host(db_host)
    db_port = _safe_db_port(db_port)
    db_user = _safe_db_user(db_user)
  else:
    database_name = ''

  return ProjectCreateNormalizedInput(
    project_name=project_name,
    python_version=python_version,
    conda_name=conda_name,
    use_database=use_database,
    database_name=database_name,
    db_host=db_host,
    db_port=db_port,
    db_user=db_user,
    db_password=db_password,
    base_path=base_path,
    use_nginx=use_nginx,
    server_ip=server_ip,
    nginx_server_ip=nginx_server_ip,
    requested_nginx_conf_path=requested_nginx_conf_path,
    confirmed_nginx_config_text=confirmed_nginx_config_text,
  )


def parse_conda_env_paths(output: str) -> list[str]:
  """从 `conda env list --json` 输出中解析环境路径列表。

  参数：
  - output：Conda JSON 输出文本。

  返回：
  - list[str]：环境路径列表；格式异常时抛出 HTTP 500。
  """
  try:
    conda_data = json.loads(output or '{}')
    conda_envs = conda_data.get('envs') if isinstance(conda_data, dict) else []
    if not isinstance(conda_envs, list):
      return []
    return [str(item) for item in conda_envs]
  except Exception as ex:
    raise HTTPException(status_code=500, detail=f'解析Conda环境列表失败：{str(ex)}')


def conda_env_exists(conda_env_paths: list[str], conda_name: str) -> bool:
  """判断 Conda 环境路径列表中是否已存在指定环境名。"""
  suffix = f'/{conda_name}'
  return any(str(item).rstrip('/').endswith(suffix) for item in conda_env_paths)
