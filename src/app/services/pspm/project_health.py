"""项目运行状态和健康检测服务。

本模块集中维护项目列表轻量运行状态检测、单项目完整健康检测、ORM 到响应模型转换等逻辑。
创建/设置弹框中的即时校验逻辑保留在 project_checks.py 中。
"""

from __future__ import annotations

import shlex

from app import crud, schemas
from app.services.pspm.project_helpers import get_project_for_user, list_conda_env_names_on_server
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _safe_db_host,
  _safe_db_port,
  _safe_db_user,
)
from app.utils.pspm.nginx_utils import _is_nginx_running_on_server
from app.utils.pspm.project_config import DEFAULT_MYSQL_PORT
from app.utils.pspm.project_health_messages import (
  HEALTH_DATABASE_NAME_PREFIX,
  HEALTH_DETAIL_SEPARATOR,
  HEALTH_EMPTY_TEXT,
  HEALTH_NGINX_BACKEND_PREFIX,
  HEALTH_NGINX_FRONTEND_PREFIX,
  HEALTH_SERVICE_RUNNING,
  HEALTH_SERVICE_STOPPED,
  HEALTH_STATUS_ERROR,
  HEALTH_STATUS_NORMAL,
  HEALTH_STATUS_UNCHECKED,
  HEALTH_SUMMARY_SEPARATOR,
  health_pair,
  health_problem,
)
from app.utils.pspm.runtime_utils import _inspect_project_runtime
from app.utils.pspm.shell_utils import _list_allowed_server_rows, _run_server_shell


def _join_summary_parts(parts: list[str], empty_text: str = HEALTH_EMPTY_TEXT) -> str:
  """拼接项目列表复合字段的已配置部分。"""
  values = [str(item or '').strip() for item in parts if str(item or '').strip()]
  return HEALTH_SUMMARY_SEPARATOR.join(values) if values else empty_text

def _normalize_service_runtime(runtime_data: dict | None) -> tuple[str, str]:
  """规范服务状态和运行端口，保证两者强一致。

  参数：
  - runtime_data：运行态检测结果，通常来自 `_inspect_project_runtime`。

  规则：
  - 只有状态为运行中且端口非空时，才允许返回运行中。
  - 其他情况一律返回已停止和空端口，避免页面出现“运行中但无端口”。
  """
  data = runtime_data or {}
  service_status = str(data.get('service_status') or '').strip()
  running_port = str(data.get('running_port') or '').strip()
  if service_status == HEALTH_SERVICE_RUNNING and running_port:
    return HEALTH_SERVICE_RUNNING, running_port
  return HEALTH_SERVICE_STOPPED, ''

async def _server_path_exists(server_row, path: str) -> bool:
  """检查指定业务服务器上的路径是否存在。"""
  value = str(path or '').strip()
  if not value:
    return False
  code, _out, _err = await _run_server_shell(server_row, f'test -e {shlex.quote(value)}', timeout=10)
  return code == 0

async def _server_conda_env_exists(server_row, env_name: str) -> bool:
  """检查指定业务服务器上的 Conda 环境是否存在。"""
  value = str(env_name or '').strip()
  if not value:
    return False
  try:
    envs = await list_conda_env_names_on_server(server_row)
  except Exception:
    return False
  return value in envs

async def _nginx_conf_contains_project_config(server_row, conf_path: str, frontend_port: str, backend_port: str) -> bool:
  """检查 Nginx 配置文件是否包含项目配置的 listen 和 proxy_pass 端口。"""
  path = str(conf_path or '').strip()
  if not path:
    return False
  quoted_path = shlex.quote(path)
  checks: list[str] = [f'test -f {quoted_path}']
  if str(frontend_port or '').strip():
    port = shlex.quote(str(frontend_port).strip())
    checks.append(f'grep -E "listen[[:space:]]+{port}([^0-9]|;)" {quoted_path} >/dev/null')
  if str(backend_port or '').strip():
    port = shlex.quote(str(backend_port).strip())
    checks.append(f'grep -E "proxy_pass[[:space:]]+http://[^;:]+:{port}(/|;|[^0-9])" {quoted_path} >/dev/null')
  code, _out, _err = await _run_server_shell(server_row, ' && '.join(checks), timeout=15)
  return code == 0

async def inspect_projects_runtime_service(session, current_user, result: schemas.pspm.ProjectItems) -> schemas.pspm.ProjectItems:
  """轻量级项目列表运行状态检测服务。

  项目列表刷新时不对每一行执行完整健康检查，只补充服务状态和运行端口。
  完整健康检查仍由前端按钮触发，避免列表加载耗时过长。
  """
  if not result.data:
    return result

  servers = await _list_allowed_server_rows(session, current_user)
  server_by_id = {int(getattr(item, 'id', 0) or 0): item for item in servers.data}
  server_by_ip = {str(getattr(item, 'ip', '') or '').strip(): item for item in servers.data}

  for item in result.data:
    db_status = str(item.status or '').strip() or HEALTH_SERVICE_STOPPED
    item.service_status = db_status
    item.running_port = ''
    if db_status != HEALTH_SERVICE_RUNNING:
      continue

    server_row = server_by_id.get(int(item.server_id or 0)) or server_by_ip.get(str(item.server_ip or '').strip())
    if not server_row:
      continue

    runtime_data = await _inspect_project_runtime(server_row, item)
    item.service_status, item.running_port = _normalize_service_runtime(runtime_data)
    item.status = item.service_status

  return result

async def inspect_projects_health_service(session, current_user, result: schemas.pspm.ProjectItems) -> schemas.pspm.ProjectItems:
  """为项目列表补充服务状态、运行端口和项目健康状态。"""
  if not result.data:
    return result

  servers = await _list_allowed_server_rows(session, current_user)
  server_by_id = {int(getattr(item, 'id', 0) or 0): item for item in servers.data}
  server_by_ip = {str(getattr(item, 'ip', '') or '').strip(): item for item in servers.data}

  for item in result.data:
    problems: list[str] = []
    server_row = server_by_id.get(int(item.server_id or 0)) or server_by_ip.get(str(item.server_ip or '').strip())

    if not server_row:
      problems.append(health_problem('project_server_unavailable'))
      item.service_status = HEALTH_SERVICE_STOPPED
      item.running_port = ''
    else:
      runtime_data = await _inspect_project_runtime(server_row, item)
      item.service_status, item.running_port = _normalize_service_runtime(runtime_data)

      if item.backend_path and not await _server_path_exists(server_row, item.backend_path):
        problems.append(health_problem('project_dir_missing'))
      if item.conda_env_name and not await _server_conda_env_exists(server_row, item.conda_env_name):
        problems.append(health_problem('conda_missing'))

    if item.database_name:
      try:
        host = _safe_db_host(item.database_host or '')
        port = _safe_db_port(int(item.database_port or DEFAULT_MYSQL_PORT))
        user = _safe_db_user(item.database_user or '')
        password = str(item.database_password or '')
        ok, _message = await _check_server_mysql_connectable(host, port, user, password)
        if not ok:
          problems.append(health_problem('database_connect_failed'))
        elif not await _check_database_exists(host, port, user, password, item.database_name):
          problems.append(health_problem('database_missing'))
      except Exception:
        problems.append(health_problem('database_check_failed'))

    if item.nginx_conf_path or item.nginx_server_ip or item.frontend_port or item.backend_deploy_port:
      nginx_row = server_by_ip.get(str(item.nginx_server_ip or '').strip()) or server_row
      if not nginx_row:
        problems.append(health_problem('nginx_server_unavailable'))
      elif not await _is_nginx_running_on_server(nginx_row):
        problems.append(health_problem('nginx_not_running'))
      elif item.nginx_conf_path:
        ok_conf = await _nginx_conf_contains_project_config(
          nginx_row,
          item.nginx_conf_path,
          item.frontend_port or '',
          item.backend_deploy_port or '',
        )
        if not ok_conf:
          problems.append(health_problem('nginx_config_mismatch'))

    item.nginx_info = _join_summary_parts([
      item.nginx_server_ip or '',
      health_pair(HEALTH_NGINX_FRONTEND_PREFIX, item.frontend_port or ''),
      health_pair(HEALTH_NGINX_BACKEND_PREFIX, item.backend_deploy_port or ''),
    ])
    item.database_info = _join_summary_parts([
      item.database_host or '',
      health_pair(HEALTH_DATABASE_NAME_PREFIX, item.database_name or ''),
    ])
    item.project_status = HEALTH_STATUS_ERROR if problems else HEALTH_STATUS_NORMAL
    item.project_status_detail = HEALTH_DETAIL_SEPARATOR.join(problems)
    item.status = item.service_status

  return result

def _project_schema_from_orm(project, owner_name: str = '', server_ip: str | None = None) -> schemas.pspm.ProjectItem:
  """把项目 ORM 对象转换成项目列表行 schema。

  参数：
  - project：项目 ORM 对象。
  - owner_name：项目所属用户账号；为空时使用 user_{owner_id}。
  - server_ip：项目服务器 IP；为空时前端展示为未配置。

  作用：
  - 单项目健康检测接口需要复用列表行结构，避免返回字段和列表不一致。

  返回：
  - ProjectItem：包含项目基础字段、汇总字段和默认未检测状态。
  """
  item = schemas.pspm.ProjectItem(
    id=project.id,
    owner_id=project.owner_id,
    owner=owner_name or f'user_{project.owner_id}',
    name=project.name,
    description=project.description,
    server_id=project.server_id,
    server_ip=server_ip,
    backend_path=project.backend_path,
    frontend_path=project.frontend_path,
    nginx_conf_path=project.nginx_conf_path,
    nginx_server_ip=getattr(project, 'nginx_server_ip', None),
    nginx_config_text=getattr(project, 'nginx_config_text', None),
    frontend_port=project.frontend_port,
    backend_dev_port=project.backend_dev_port,
    backend_deploy_port=project.backend_deploy_port,
    database_name=project.database_name,
    database_host=getattr(project, 'database_host', None),
    database_port=getattr(project, 'database_port', None),
    database_user=getattr(project, 'database_user', None),
    database_password=getattr(project, 'database_password', None),
    conda_env_name=project.conda_env_name,
    python_version=project.python_version,
    dev_start_command=project.dev_start_command,
    deploy_start_command=project.deploy_start_command,
    entry_file_path=project.entry_file_path,
    status=crud.projects.model and crud.project_status_to_name(project.status) if False else (HEALTH_SERVICE_RUNNING if project.status == 1 else HEALTH_SERVICE_STOPPED),
    created_at=project.created_at,
  )
  item.nginx_info = _join_summary_parts([
    item.nginx_server_ip or '',
    health_pair(HEALTH_NGINX_FRONTEND_PREFIX, item.frontend_port or ''),
    health_pair(HEALTH_NGINX_BACKEND_PREFIX, item.backend_deploy_port or ''),
  ])
  item.database_info = _join_summary_parts([
    item.database_host or '',
    health_pair(HEALTH_DATABASE_NAME_PREFIX, item.database_name or ''),
  ])
  item.project_status = HEALTH_STATUS_UNCHECKED
  item.project_status_detail = ''
  item.service_status = item.status
  item.running_port = ''
  return item

async def inspect_project_health_service(session, current_user, project_id: int) -> schemas.pspm.ProjectItem:
  """按需检测单个项目的健康状态。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：前端点击“检测状态”按钮时传入的项目 ID。

  作用：
  - 只检测当前项目，避免列表刷新时批量连接服务器、数据库和 Nginx 导致页面慢或整体失败。
  - 检测项目目录、Conda 环境、数据库、Nginx 配置和服务运行端口。

  返回：
  - ProjectItem：与列表行同结构，但 project_status / project_status_detail / running_port 为本次检测结果。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  servers = await _list_allowed_server_rows(session, current_user)
  server_by_id = {int(getattr(item, 'id', 0) or 0): item for item in servers.data}
  server_by_ip = {str(getattr(item, 'ip', '') or '').strip(): item for item in servers.data}
  server_row = server_by_id.get(int(getattr(project, 'server_id', None) or 0))
  server_ip = str(getattr(server_row, 'ip', '') or '').strip() if server_row else ''

  item = _project_schema_from_orm(project, owner_name=str(getattr(current_user, 'username', '') or ''), server_ip=server_ip or None)
  problems: list[str] = []

  if not server_row:
    problems.append(health_problem('project_server_unavailable'))
    item.service_status = HEALTH_SERVICE_STOPPED
    item.running_port = ''
  else:
    runtime_data = await _inspect_project_runtime(server_row, item)
    item.service_status, item.running_port = _normalize_service_runtime(runtime_data)
    await crud.projects.update_status(session, project_id=project_id, running=item.service_status == HEALTH_SERVICE_RUNNING)
    if item.backend_path and not await _server_path_exists(server_row, item.backend_path):
      problems.append(health_problem('project_dir_missing'))
    if item.conda_env_name and not await _server_conda_env_exists(server_row, item.conda_env_name):
      problems.append(health_problem('conda_missing'))

  if item.database_name:
    try:
      host = _safe_db_host(item.database_host or '')
      port = _safe_db_port(int(item.database_port or DEFAULT_MYSQL_PORT))
      user = _safe_db_user(item.database_user or '')
      password = str(item.database_password or '')
      ok, _message = await _check_server_mysql_connectable(host, port, user, password)
      if not ok:
        problems.append(health_problem('database_connect_failed'))
      elif not await _check_database_exists(host, port, user, password, item.database_name):
        problems.append(health_problem('database_missing'))
    except Exception:
      problems.append(health_problem('database_check_failed'))

  if item.nginx_conf_path or item.nginx_server_ip or item.frontend_port or item.backend_deploy_port:
    nginx_row = server_by_ip.get(str(item.nginx_server_ip or '').strip()) or server_row
    if not nginx_row:
      problems.append(health_problem('nginx_server_unavailable'))
    elif not await _is_nginx_running_on_server(nginx_row):
      problems.append(health_problem('nginx_not_running'))
    elif item.nginx_conf_path:
      ok_conf = await _nginx_conf_contains_project_config(
        nginx_row,
        item.nginx_conf_path,
        item.frontend_port or '',
        item.backend_deploy_port or '',
      )
      if not ok_conf:
        problems.append(health_problem('nginx_config_mismatch'))

  item.project_status = HEALTH_STATUS_ERROR if problems else HEALTH_STATUS_NORMAL
  item.project_status_detail = HEALTH_DETAIL_SEPARATOR.join(problems)
  item.status = item.service_status
  return item
