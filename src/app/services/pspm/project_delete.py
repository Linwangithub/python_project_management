import shlex
from typing import List

from fastapi import HTTPException

from app import crud
from app.services.pspm.project_helpers import ensure_safe_project_delete_path
from app.utils.pspm.db_utils import _drop_database_if_exists, _safe_db_host, _safe_db_identifier, _safe_db_port, _safe_db_user
from app.utils.pspm.nginx_utils import (
  _apply_nginx_conf_change_on_server,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _remove_project_server_blocks,
)
from app.utils.pspm.project_config import (
  CONDA_INIT,
  DELETE_SCOPE_OPTIONS,
  DELETE_SCOPE_PROJECT_AND_CONDA,
  DELETE_SCOPE_PROJECT_CONDA_AND_DB,
  DELETE_SCOPE_PROJECT_CONDA_DB_NGINX,
  DELETE_SCOPE_PROJECT_CONDA_NGINX,
  DELETE_SCOPE_PROJECT_ONLY,
)
from app.utils.pspm.shell_utils import _find_project_nginx_server_row, _list_allowed_server_rows, _run_shell


async def load_deletable_projects(session, current_user, ids: List[int]):
  """加载待删除项目并校验普通用户权限。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - ids：前端删除弹框提交的项目 ID 列表。

  作用：
  - root 可以删除所有项目。
  - 普通用户只能删除自己的项目。

  返回：
  - 项目 ORM 对象列表。
  """
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  if not is_root:
    projects = await crud.projects.get_items(
      session,
      current_user_id=current_user.id,
      is_root=False,
      page=1,
      page_size=500,
    )
    allowed_ids = {row.id for row in projects.data}
    for item in ids:
      if item not in allowed_ids:
        raise HTTPException(status_code=403, detail='包含无权限删除的项目')

  project_rows = []
  for item in ids:
    row = await crud.projects.get(session, obj_in={'id': item, 'status': [0, 1]})
    if not row:
      raise HTTPException(status_code=404, detail=f'项目不存在或已删除：{item}')
    project_rows.append(row)
  return project_rows


async def delete_project_dirs(project_rows):
  """删除项目目录。

  参数：
  - project_rows：待删除项目列表。

  作用：
  - 所有删除范围都包含项目目录删除。
  - 删除前调用 `ensure_safe_project_delete_path` 防止误删系统目录。
  """
  for row in project_rows:
    backend_path = ensure_safe_project_delete_path(row.backend_path)
    if not backend_path:
      continue
    code, _out, err = await _run_shell(f'rm -rf {shlex.quote(backend_path)}', timeout=600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'删除项目目录失败：{backend_path} {err.strip() or ""}'.strip())


async def delete_conda_envs_if_needed(project_rows, delete_scope: str):
  """按删除范围删除 Conda 环境。

  参数：
  - project_rows：待删除项目列表。
  - delete_scope：删除范围，来自 Query 参数。

  作用：
  - 只有选择“项目+Conda”及更大范围时才删除 Conda 环境。
  """
  if delete_scope not in {DELETE_SCOPE_PROJECT_AND_CONDA, DELETE_SCOPE_PROJECT_CONDA_AND_DB, DELETE_SCOPE_PROJECT_CONDA_NGINX, DELETE_SCOPE_PROJECT_CONDA_DB_NGINX}:
    return

  for row in project_rows:
    conda_name = (row.conda_env_name or '').strip()
    if not conda_name:
      continue
    cmd = f'{CONDA_INIT}; conda env remove -n {shlex.quote(conda_name)} -y'
    code, _out, err = await _run_shell(cmd, timeout=3600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'删除Conda环境失败：{conda_name} {err.strip() or ""}'.strip())


async def delete_databases_if_needed(project_rows, delete_scope: str):
  """按删除范围删除项目数据库。

  参数：
  - project_rows：待删除项目列表。
  - delete_scope：删除范围，来自 Query 参数。

  作用：
  - 选择“项目+Conda+数据库”及更大范围时，删除项目配置的数据库。
  - 优先使用项目表保存的数据库连接信息，避免误删当前系统库。
  """
  if delete_scope not in {DELETE_SCOPE_PROJECT_CONDA_AND_DB, DELETE_SCOPE_PROJECT_CONDA_DB_NGINX}:
    return

  for row in project_rows:
    db_name = (row.database_name or '').strip()
    if not db_name:
      continue
    safe_name = _safe_db_identifier(db_name)
    db_host = _safe_db_host(str(getattr(row, 'database_host', '') or 'localhost'))
    db_port = _safe_db_port(int(str(getattr(row, 'database_port', '') or '3306')))
    db_user = _safe_db_user(str(getattr(row, 'database_user', '') or 'root'))
    db_password = str(getattr(row, 'database_password', '') or '')
    await _drop_database_if_exists(db_host, db_port, db_user, db_password, safe_name)


async def delete_nginx_blocks_if_needed(session, current_user, project_rows, delete_scope: str):
  """按删除范围删除 Nginx 配置中的项目 server block。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_rows：待删除项目列表。
  - delete_scope：删除范围，来自 Query 参数。

  作用：
  - 只有选择“项目+Conda+数据库+Nginx配置”时才执行。
  - 每次修改 Nginx 配置都会走工具函数中的备份、语法检查、reload、失败回滚流程。
  """
  if delete_scope not in {DELETE_SCOPE_PROJECT_CONDA_NGINX, DELETE_SCOPE_PROJECT_CONDA_DB_NGINX}:
    return

  servers = await _list_allowed_server_rows(session, current_user)
  for row in project_rows:
    nginx_server_row = _find_project_nginx_server_row(servers, row)
    if not nginx_server_row:
      raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')
    running = await _is_nginx_running_on_server(nginx_server_row)
    if not running:
      raise HTTPException(status_code=400, detail='nginx服务未开启')
    conf_path = str(row.nginx_conf_path or '').strip() or await _get_running_nginx_conf_path_on_server(nginx_server_row)
    ok, msg = await _apply_nginx_conf_change_on_server(
      nginx_server_row,
      conf_path,
      lambda old, project_name=str(row.name or '').strip(): _remove_project_server_blocks(old, project_name)[0],
    )
    if not ok:
      raise HTTPException(status_code=500, detail=f'删除nginx配置失败：{msg}')


async def delete_project_service(session, current_user, ids: List[int], delete_scope: str) -> str:
  """删除项目及用户选择的关联资源。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - ids：项目 ID 列表，来自 Query 参数 `id`。
  - delete_scope：删除范围，来自 Query 参数。

  作用：
  - 根据用户选择，依次删除项目目录、Conda 环境、数据库、Nginx 配置。
  - 最后软删除项目表记录。

  返回：
  - 删除成功提示文案。
  """
  if delete_scope not in DELETE_SCOPE_OPTIONS:
    raise HTTPException(status_code=400, detail='删除范围不合法')

  project_rows = await load_deletable_projects(session, current_user, ids)
  await delete_project_dirs(project_rows)
  await delete_conda_envs_if_needed(project_rows, delete_scope)
  await delete_databases_if_needed(project_rows, delete_scope)
  await delete_nginx_blocks_if_needed(session, current_user, project_rows, delete_scope)

  rows = await crud.projects.remove_multi(session, ids=ids)
  if rows <= 0:
    raise HTTPException(status_code=400, detail='删除失败')

  scope_label_map = {
    DELETE_SCOPE_PROJECT_ONLY: '只删除项目',
    DELETE_SCOPE_PROJECT_AND_CONDA: '删除项目+Conda环境',
    DELETE_SCOPE_PROJECT_CONDA_AND_DB: '删除项目+Conda环境+数据库',
    DELETE_SCOPE_PROJECT_CONDA_NGINX: '删除项目+Conda环境+Nginx配置',
    DELETE_SCOPE_PROJECT_CONDA_DB_NGINX: '删除项目+Conda环境+数据库+Nginx配置',
  }
  return f'删除成功（{scope_label_map.get(delete_scope, delete_scope)}）'
