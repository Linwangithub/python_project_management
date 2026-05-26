"""项目创建服务模块，负责创建项目记录并按配置执行目录、Conda、数据库和 Nginx 初始化。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import os
import shlex
from typing import List

from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_helpers import frontend_dist_base_dir_for_user, frontend_root_for_project
from app.services.pspm.project_create_helpers import (
  conda_env_exists,
  normalize_project_create_input,
  parse_conda_env_paths,
)
from app.services.pspm.project_detail import record_project_operation, snapshot_project_config
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _create_database_utf8mb4,
  _drop_database_if_exists,
)
from app.utils.pspm.nginx_utils import (
  _apply_nginx_conf_change_on_server,
  _check_nginx_port_conflict_on_server,
  _collect_nginx_conf_inventory_on_server,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _is_port_in_use_on_server,
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.nginx_server_blocks import (
  _normalize_confirmed_nginx_server_block,
  _remove_project_server_blocks,
  _replace_or_append_project_server_block,
)
from app.utils.pspm.path_utils import (
  _build_target_dir,
  _safe_optional_port_text,
)
from app.utils.pspm.project_config import CONDA_INIT, DEFAULT_FRONTEND_PATH
from app.utils.pspm.project_create_messages import (
  NGINX_PORT_REQUIRED_MESSAGE,
  NGINX_PORT_SAME_MESSAGE,
  NGINX_SERVER_PERMISSION_DENIED_MESSAGE,
  NGINX_SERVICE_NOT_RUNNING_MESSAGE,
  PROJECT_CREATE_LOG_ACTION,
  PROJECT_CREATE_LOG_ACTION_LABEL,
  PROJECT_CREATE_SUCCESS_STATUS,
  PROJECT_NAME_EXISTS_MESSAGE,
  PROJECT_SERVER_PERMISSION_DENIED_MESSAGE,
  UNKNOWN_ERROR_MESSAGE,
  render_project_create_message,
)
from app.utils.pspm.shell_utils import (
  _assert_server_ip_allowed,
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _ping_from_server_to_target,
  _run_shell,
  _split_lines,
)


async def create_project_record_service(session, current_user, payload: schemas.pspm.ProjectCreate):
  """创建项目基础记录。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload：`ProjectCreate` 请求体，来自旧版 `/create` 接口。

  作用：
  - 该接口只写项目表记录，不真实创建目录、Conda、数据库、Nginx。
  - 当前主要兼容旧前端或调试请求。

  返回：
  - 无业务数据；创建成功时由接口层返回 `BaseResponse`。
  """
  exists = await crud.projects.get(session, obj_in={'owner_id': current_user.id, 'name': payload.name, 'status': [0, 1]})
  if exists:
    raise HTTPException(status_code=400, detail=PROJECT_NAME_EXISTS_MESSAGE)

  await crud.projects.create(
    session,
    obj_in={
      'owner_id': current_user.id,
      'server_id': payload.server_id,
      'name': payload.name,
      'description': payload.description,
      'backend_path': payload.backend_path,
      'frontend_path': payload.frontend_path,
      'nginx_conf_path': payload.nginx_conf_path,
      'nginx_server_ip': payload.nginx_server_ip,
      'frontend_port': payload.frontend_port,
      'backend_dev_port': payload.backend_dev_port,
      'backend_deploy_port': payload.backend_deploy_port,
      'database_name': payload.database_name,
      'database_host': getattr(payload, 'database_host', None),
      'database_port': str(getattr(payload, 'database_port', '') or '') or None,
      'database_user': getattr(payload, 'database_user', None),
      'database_password': getattr(payload, 'database_password', None),
      'conda_env_name': payload.conda_env_name,
      'python_version': payload.python_version,
      'dev_start_command': payload.dev_start_command,
      'deploy_start_command': payload.deploy_start_command,
      'entry_file_path': payload.entry_file_path,
      'status': 0,
      'created_by': current_user.id,
    },
  )


async def create_project_real_service(session, current_user, payload: schemas.pspm.ProjectRealCreateRequest):
  """真实创建项目目录、Conda 环境、数据库、Nginx 配置和项目表记录。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload：`ProjectRealCreateRequest` 请求体，来自新建项目弹框。

  业务流程：
  1. 校验项目名、Python 版本、Conda 名、项目路径、服务器权限。
  2. 如果启用数据库，校验连接信息并确认目标数据库不存在。
  3. 如果启用 Nginx，校验 Nginx 服务器可达、服务运行、配置文件路径合法、端口未占用。
  4. 创建项目目录。
  5. 创建 Conda 环境并校验 Python 版本。
  6. 可选创建数据库。
  7. 可选写入 Nginx server block。
  8. 写入项目表记录。

  强一致性：
  - 任一步失败时，会尽量回滚已创建的项目记录、数据库、Nginx 配置、Conda 环境和项目目录。

  返回：
  - `ProjectRealCreateResponseData`，包含项目 ID、后端路径、Conda 环境、Python 版本和执行日志。
  """
  normalized = normalize_project_create_input(payload)
  project_name = normalized.project_name
  python_version = normalized.python_version
  conda_name = normalized.conda_name
  use_database = normalized.use_database
  database_name = normalized.database_name
  db_host = normalized.db_host
  db_port = normalized.db_port
  db_user = normalized.db_user
  db_password = normalized.db_password
  base_path = normalized.base_path
  use_nginx = normalized.use_nginx
  server_ip = normalized.server_ip
  nginx_server_ip = normalized.nginx_server_ip
  requested_nginx_conf_path = normalized.requested_nginx_conf_path
  confirmed_nginx_config_text = normalized.confirmed_nginx_config_text
  _assert_server_ip_allowed(server_ip)

  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_ip(servers, server_ip)
  if not server_row:
    raise HTTPException(status_code=403, detail=PROJECT_SERVER_PERMISSION_DENIED_MESSAGE)

  nginx_conf_path = ''
  nginx_frontend_port = ''
  nginx_backend_port = ''
  nginx_server_row = None
  running_conf_path = ''
  is_root_user = await crud.rbac.is_root_user(session, user_id=current_user.id)
  frontend_dist_base_dir = frontend_dist_base_dir_for_user(current_user, is_root_user)
  frontend_project_root = frontend_root_for_project(current_user, is_root_user, project_name)

  if use_nginx:
    nginx_server_row = _find_server_row_by_ip(servers, nginx_server_ip)
    if not nginx_server_row:
      raise HTTPException(status_code=403, detail=NGINX_SERVER_PERMISSION_DENIED_MESSAGE)

    ping_ok, ping_msg = await _ping_from_server_to_target(server_row, nginx_server_ip)
    if not ping_ok:
      raise HTTPException(status_code=400, detail=render_project_create_message('nginx_unreachable', message=ping_msg))

    running = await _is_nginx_running_on_server(nginx_server_row)
    if not running:
      raise HTTPException(status_code=400, detail=NGINX_SERVICE_NOT_RUNNING_MESSAGE)
    running_conf_path = await _get_running_nginx_conf_path_on_server(nginx_server_row)
    inventory = await _collect_nginx_conf_inventory_on_server(nginx_server_row, running_conf_path)
    nginx_conf_path = _validate_requested_nginx_conf_path(requested_nginx_conf_path, inventory)

  exists_db = await crud.projects.get(session, obj_in={'owner_id': current_user.id, 'name': project_name, 'status': [0, 1]})
  if exists_db:
    raise HTTPException(status_code=400, detail=PROJECT_NAME_EXISTS_MESSAGE)

  target_dir = _build_target_dir(base_path, project_name)
  if os.path.exists(target_dir):
    raise HTTPException(status_code=400, detail=render_project_create_message('directory_exists', path=target_dir))

  conda_list_cmd = f'{CONDA_INIT}; conda env list --json'
  code, out, err = await _run_shell(conda_list_cmd, timeout=120)
  if code != 0:
    msg = err.strip() or out.strip() or UNKNOWN_ERROR_MESSAGE
    raise HTTPException(status_code=500, detail=render_project_create_message('conda_query_failed', message=msg))

  conda_envs = parse_conda_env_paths(out)
  if conda_env_exists(conda_envs, conda_name):
    raise HTTPException(status_code=400, detail=render_project_create_message('conda_exists', name=conda_name))

  if use_database and database_name:
    exists = await _check_database_exists(db_host, db_port, db_user, db_password, database_name)
    if exists:
      raise HTTPException(status_code=400, detail=render_project_create_message('database_exists', name=database_name))

  if use_nginx:
    if not payload.frontend_port or not payload.backend_deploy_port:
      raise HTTPException(status_code=400, detail=NGINX_PORT_REQUIRED_MESSAGE)
    nginx_frontend_port = _safe_optional_port_text(payload.frontend_port)
    nginx_backend_port = _safe_optional_port_text(payload.backend_deploy_port)
    if nginx_frontend_port == nginx_backend_port:
      raise HTTPException(status_code=400, detail=NGINX_PORT_SAME_MESSAGE)

    frontend_port_int = int(nginx_frontend_port)
    backend_port_int = int(nginx_backend_port)
    if await _is_port_in_use_on_server(nginx_server_row, frontend_port_int):
      raise HTTPException(status_code=400, detail=render_project_create_message('frontend_port_system_used', port=frontend_port_int))
    if await _is_port_in_use_on_server(nginx_server_row, backend_port_int):
      raise HTTPException(status_code=400, detail=render_project_create_message('backend_port_system_used', port=backend_port_int))
    frontend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, frontend_port_int, running_conf_path, project_name=project_name)
    if frontend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=render_project_create_message('frontend_port_listen_used', port=frontend_port_int))
    if frontend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=render_project_create_message('frontend_port_proxy_used', port=frontend_port_int))
    backend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, backend_port_int, running_conf_path, project_name=project_name)
    if backend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=render_project_create_message('backend_port_listen_used', port=backend_port_int))
    if backend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=render_project_create_message('backend_port_proxy_used', port=backend_port_int))

    confirmed_nginx_config_text = _normalize_confirmed_nginx_server_block(
      confirmed_nginx_config_text,
      project_name,
      frontend_port_int,
      backend_port_int,
    )

  logs: List[str] = []
  actions: List[str] = []
  dir_created = False
  frontend_dist_created = False
  conda_created = False
  db_created = False
  nginx_created = False
  project_row_id: int | None = None

  mkdir_cmd = f'mkdir -p {shlex.quote(target_dir)}'
  mkdir_frontend_dist_cmd = f'mkdir -p {shlex.quote(frontend_dist_base_dir)}'
  conda_cmd = f'{CONDA_INIT}; conda create -n {shlex.quote(conda_name)} python={shlex.quote(python_version)} -y'
  py_ver_cmd = f'{CONDA_INIT}; conda run -n {shlex.quote(conda_name)} python --version'
  conda_remove_cmd = f'{CONDA_INIT}; conda env remove -n {shlex.quote(conda_name)} -y'
  rm_dir_cmd = f'rm -rf {shlex.quote(target_dir)}'

  async def rollback_all() -> List[str]:
    """回滚创建项目过程中已经完成的副作用。

    作用：
    - 保证创建项目流程尽量满足“要么都成功，要么都失败”。
    - 回滚顺序与创建顺序相反，先删数据库记录和 Nginx，再删 Conda 和目录。

    返回：
    - 回滚失败信息列表；为空表示回滚没有发现异常。
    """
    rollback_errors: List[str] = []

    if project_row_id is not None:
      try:
        rows = await crud.projects.remove(session, obj_in={'id': project_row_id})
        if not rows:
          rollback_errors.append(render_project_create_message('rollback_project_record_missing', project_id=project_row_id))
        else:
          logs.append(render_project_create_message('rollback_project_record_deleted', project_id=project_row_id))
      except Exception as ex:
        rollback_errors.append(render_project_create_message('rollback_project_record_failed', message=str(ex)))

    if db_created and use_database and database_name:
      try:
        await _drop_database_if_exists(db_host, db_port, db_user, db_password, database_name)
        logs.append(render_project_create_message('rollback_database_deleted', database_name=database_name))
      except Exception as ex:
        rollback_errors.append(render_project_create_message('rollback_database_failed', message=str(ex)))

    if nginx_created and use_nginx and nginx_conf_path and nginx_server_row is not None:
      try:
        ok_rb, msg_rb = await _apply_nginx_conf_change_on_server(
          nginx_server_row,
          nginx_conf_path,
          lambda old: _remove_project_server_blocks(old, project_name)[0],
        )
        if ok_rb:
          logs.append(render_project_create_message('rollback_nginx_deleted', project_name=project_name))
        else:
          rollback_errors.append(render_project_create_message('rollback_nginx_failed', message=msg_rb))
      except Exception as ex:
        rollback_errors.append(render_project_create_message('rollback_nginx_failed', message=str(ex)))

    if conda_created:
      logs.append(f'$ conda env remove -n {conda_name} -y')
      code_rb, out_rb, err_rb = await _run_shell(conda_remove_cmd, timeout=3600)
      logs.extend(_split_lines(out_rb))
      logs.extend(_split_lines(err_rb))
      if code_rb != 0:
        rollback_errors.append(render_project_create_message('rollback_conda_failed', message=err_rb.strip() or out_rb.strip() or UNKNOWN_ERROR_MESSAGE))
      else:
        logs.append(render_project_create_message('rollback_conda_deleted', conda_name=conda_name))

    if frontend_dist_created and use_nginx:
      logs.append(render_project_create_message('frontend_dist_kept', path=frontend_dist_base_dir))

    if dir_created:
      logs.append(f'$ {rm_dir_cmd}')
      code_rb, out_rb, err_rb = await _run_shell(rm_dir_cmd, timeout=600)
      logs.extend(_split_lines(out_rb))
      logs.extend(_split_lines(err_rb))
      if code_rb != 0:
        rollback_errors.append(render_project_create_message('rollback_project_dir_failed', message=err_rb.strip() or out_rb.strip() or UNKNOWN_ERROR_MESSAGE))
      else:
        logs.append(render_project_create_message('rollback_project_dir_deleted', path=target_dir))

    return rollback_errors

  try:
    logs.append(f'$ mkdir -p {shlex.quote(target_dir)}')
    code, out, err = await _run_shell(mkdir_cmd, timeout=60)
    if code != 0:
      actions.append(render_project_create_message('create_project_dir_failed_action', path=target_dir))
      raise HTTPException(status_code=500, detail=render_project_create_message('create_project_dir_failed', message=err.strip() or UNKNOWN_ERROR_MESSAGE))
    dir_created = True
    actions.append(render_project_create_message('create_project_dir_success', path=target_dir))
    logs.append(render_project_create_message('create_project_dir_success', path=target_dir))

    if use_nginx:
      logs.append(f'$ mkdir -p {shlex.quote(frontend_dist_base_dir)}')
      code, out, err = await _run_shell(mkdir_frontend_dist_cmd, timeout=60)
      if code != 0:
        actions.append(render_project_create_message('create_frontend_dir_failed_action', path=frontend_dist_base_dir))
        raise HTTPException(status_code=500, detail=render_project_create_message('create_frontend_dir_failed', path=frontend_dist_base_dir, message=err.strip() or UNKNOWN_ERROR_MESSAGE))
      frontend_dist_created = True
      actions.append(render_project_create_message('create_frontend_dir_success', path=frontend_dist_base_dir))
      logs.append(render_project_create_message('create_frontend_dir_success', path=frontend_dist_base_dir))

    logs.append(render_project_create_message('create_conda_start', conda_name=conda_name, python_version=python_version))
    code, out, err = await _run_shell(conda_cmd, timeout=3600)
    if code != 0:
      actions.append(render_project_create_message('create_conda_failed_action', conda_name=conda_name, python_version=python_version))
      raise HTTPException(status_code=500, detail=render_project_create_message('create_conda_failed', message=err.strip() or UNKNOWN_ERROR_MESSAGE))
    conda_created = True
    actions.append(render_project_create_message('create_conda_success', conda_name=conda_name, python_version=python_version))
    logs.append(render_project_create_message('create_conda_success', conda_name=conda_name, python_version=python_version))

    logs.append(render_project_create_message('check_python_start', conda_name=conda_name))
    code, out, err = await _run_shell(py_ver_cmd, timeout=120)
    if code != 0:
      actions.append(render_project_create_message('check_python_failed_action', conda_name=conda_name))
      raise HTTPException(status_code=500, detail=render_project_create_message('check_python_failed', message=err.strip() or UNKNOWN_ERROR_MESSAGE))
    python_check_text = ' '.join(_split_lines(out) + _split_lines(err)).strip() or python_version
    actions.append(render_project_create_message('check_python_success', python_text=python_check_text))
    logs.append(render_project_create_message('check_python_success', python_text=python_check_text))

    if use_database and database_name:
      mysql_ok, mysql_msg = await _check_server_mysql_connectable(db_host, db_port, db_user, db_password)
      if not mysql_ok:
        actions.append(render_project_create_message('create_database_failed_mysql_action', database_name=database_name))
        raise HTTPException(status_code=500, detail=render_project_create_message('create_database_failed_mysql', message=mysql_msg))
      try:
        await _create_database_utf8mb4(db_host, db_port, db_user, db_password, database_name)
      except Exception as ex:
        actions.append(render_project_create_message('create_database_failed_action', database_name=database_name))
        raise HTTPException(status_code=500, detail=render_project_create_message('create_database_failed', message=str(ex)))
      db_created = True
      actions.append(render_project_create_message('create_database_success', database_name=database_name, host=db_host, port=db_port))
      logs.append(render_project_create_message('create_database_success', database_name=database_name, host=db_host, port=db_port))

    if use_nginx:
      ok_nginx, msg_nginx = await _apply_nginx_conf_change_on_server(
        nginx_server_row,
        nginx_conf_path,
        lambda old: _replace_or_append_project_server_block(old, project_name, confirmed_nginx_config_text),
      )
      if not ok_nginx:
        actions.append(render_project_create_message('write_nginx_failed_action', path=nginx_conf_path))
        raise HTTPException(status_code=500, detail=render_project_create_message('create_nginx_failed', message=msg_nginx))
      nginx_created = True
      actions.append(render_project_create_message('write_nginx_success', path=nginx_conf_path, frontend_port=nginx_frontend_port, backend_port=nginx_backend_port))
      logs.append(render_project_create_message('write_nginx_success', path=nginx_conf_path, frontend_port=nginx_frontend_port, backend_port=nginx_backend_port))

    created = await crud.projects.create(
      session,
      obj_in={
        'owner_id': current_user.id,
        'server_id': server_row.id,
        'name': project_name,
        'description': (payload.description or '').strip() or None,
        'backend_path': target_dir,
        'frontend_path': frontend_project_root if use_nginx else (DEFAULT_FRONTEND_PATH or None),
        'nginx_conf_path': nginx_conf_path if use_nginx else None,
        'nginx_server_ip': nginx_server_ip if use_nginx else None,
        'frontend_port': nginx_frontend_port if use_nginx else '',
        'backend_dev_port': '',
        'backend_deploy_port': nginx_backend_port if use_nginx else '',
        'database_name': database_name or None,
        'database_host': db_host if use_database else None,
        'database_port': str(db_port) if use_database and db_port else None,
        'database_user': db_user if use_database else None,
        'database_password': db_password if use_database else None,
        'conda_env_name': conda_name,
        'python_version': python_version,
        'dev_start_command': '',
        'deploy_start_command': '',
        'entry_file_path': '',
        'status': 0,
        'nginx_config_text': confirmed_nginx_config_text if use_nginx else None,
        'created_by': current_user.id,
      },
    )
    project_row_id = created.id

    actions.append(render_project_create_message('create_project_record_success', project_name=project_name))
    logs.append(PROJECT_CREATE_SUCCESS_STATUS)
    await record_project_operation(
      session,
      created,
      current_user,
      action=PROJECT_CREATE_LOG_ACTION,
      action_label=PROJECT_CREATE_LOG_ACTION_LABEL,
      summary=render_project_create_message('create_project_summary', project_name=project_name),
      before_data=None,
      after_data=snapshot_project_config(created, {'server_ip': str(server_row.ip or '')}),
      detail={'actions': actions},
    )
    return schemas.pspm.ProjectRealCreateResponseData(
      project_id=created.id,
      status=PROJECT_CREATE_SUCCESS_STATUS,
      backend_path=target_dir,
      conda_env_name=conda_name,
      python_version=python_version,
      logs=logs,
    )
  except HTTPException as ex:
    rollback_errors = await rollback_all()
    if rollback_errors:
      detail = render_project_create_message('rollback_failed_suffix', message=str(ex.detail), rollback_errors=' | '.join(rollback_errors))
      raise HTTPException(status_code=ex.status_code, detail=detail)
    raise
  except Exception as ex:
    rollback_errors = await rollback_all()
    detail = render_project_create_message('create_project_failed', message=str(ex))
    if rollback_errors:
      detail = render_project_create_message('rollback_failed_suffix', message=detail, rollback_errors=' | '.join(rollback_errors))
    raise HTTPException(status_code=500, detail=detail)
