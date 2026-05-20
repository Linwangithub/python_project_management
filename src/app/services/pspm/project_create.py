import json
import os
import shlex
from typing import List

from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_helpers import frontend_dist_base_dir_for_user, frontend_root_for_project
from app.services.pspm.project_detail import record_project_operation, snapshot_project_config
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _create_database_utf8mb4,
  _drop_database_if_exists,
  _safe_db_host,
  _safe_db_identifier,
  _safe_db_port,
  _safe_db_user,
  _safe_optional_db_name,
)
from app.utils.pspm.nginx_utils import (
  _apply_nginx_conf_change_on_server,
  _check_nginx_port_conflict_on_server,
  _collect_nginx_conf_inventory_on_server,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _is_port_in_use_on_server,
  _normalize_confirmed_nginx_server_block,
  _remove_project_server_blocks,
  _replace_or_append_project_server_block,
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.path_utils import (
  _build_target_dir,
  _normalize_path,
  _safe_conda_name,
  _safe_optional_port_text,
  _safe_project_name,
  _safe_python_version,
)
from app.utils.pspm.project_config import CONDA_INIT, DEFAULT_FRONTEND_PATH
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
    raise HTTPException(status_code=400, detail='项目名称已存在')

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
  _assert_server_ip_allowed(server_ip)

  if use_database:
    database_name = _safe_db_identifier(database_name_input or project_name)
    db_host = _safe_db_host(db_host)
    db_port = _safe_db_port(db_port)
    db_user = _safe_db_user(db_user)
  else:
    database_name = ''

  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_ip(servers, server_ip)
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')

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
      raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

    ping_ok, ping_msg = await _ping_from_server_to_target(server_row, nginx_server_ip)
    if not ping_ok:
      raise HTTPException(status_code=400, detail=f'Nginx服务器不可达：{ping_msg}')

    running = await _is_nginx_running_on_server(nginx_server_row)
    if not running:
      raise HTTPException(status_code=400, detail='nginx服务未开启')
    running_conf_path = await _get_running_nginx_conf_path_on_server(nginx_server_row)
    inventory = await _collect_nginx_conf_inventory_on_server(nginx_server_row, running_conf_path)
    nginx_conf_path = _validate_requested_nginx_conf_path(requested_nginx_conf_path, inventory)

  exists_db = await crud.projects.get(session, obj_in={'owner_id': current_user.id, 'name': project_name, 'status': [0, 1]})
  if exists_db:
    raise HTTPException(status_code=400, detail='项目名称已存在')

  target_dir = _build_target_dir(base_path, project_name)
  if os.path.exists(target_dir):
    raise HTTPException(status_code=400, detail=f'目录已存在：{target_dir}')

  conda_list_cmd = f'{CONDA_INIT}; conda env list --json'
  code, out, err = await _run_shell(conda_list_cmd, timeout=120)
  if code != 0:
    msg = err.strip() or out.strip() or 'unknown error'
    raise HTTPException(status_code=500, detail=f'查询Conda环境失败：{msg}')

  try:
    conda_data = json.loads(out or '{}')
    conda_envs = conda_data.get('envs') if isinstance(conda_data, dict) else []
    if not isinstance(conda_envs, list):
      conda_envs = []
  except Exception as ex:
    raise HTTPException(status_code=500, detail=f'解析Conda环境列表失败：{str(ex)}')

  conda_suffix = f'/{conda_name}'
  if any(str(item).rstrip('/').endswith(conda_suffix) for item in conda_envs):
    raise HTTPException(status_code=400, detail=f'Conda环境已存在：{conda_name}')

  if use_database and database_name:
    exists = await _check_database_exists(db_host, db_port, db_user, db_password, database_name)
    if exists:
      raise HTTPException(status_code=400, detail=f'数据库 {database_name} 已存在，创建失败')

  if use_nginx:
    if not payload.frontend_port or not payload.backend_deploy_port:
      raise HTTPException(status_code=400, detail='启用nginx时必须填写前端端口和后端部署端口')
    nginx_frontend_port = _safe_optional_port_text(payload.frontend_port)
    nginx_backend_port = _safe_optional_port_text(payload.backend_deploy_port)
    if nginx_frontend_port == nginx_backend_port:
      raise HTTPException(status_code=400, detail='Nginx前端端口和后端部署端口不能相同')

    frontend_port_int = int(nginx_frontend_port)
    backend_port_int = int(nginx_backend_port)
    if await _is_port_in_use_on_server(nginx_server_row, frontend_port_int):
      raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已被系统占用')
    if await _is_port_in_use_on_server(nginx_server_row, backend_port_int):
      raise HTTPException(status_code=400, detail=f'后端部署端口 {backend_port_int} 已被系统占用')
    frontend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, frontend_port_int, running_conf_path, project_name=project_name)
    if frontend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已在Nginx listen配置中占用')
    if frontend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已在Nginx proxy_pass配置中占用')
    backend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, backend_port_int, running_conf_path, project_name=project_name)
    if backend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=f'后端部署端口 {backend_port_int} 已在Nginx listen配置中占用')
    if backend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=f'后端部署端口 {backend_port_int} 已在Nginx proxy_pass配置中占用')

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
          rollback_errors.append(f'回滚项目记录失败：记录不存在（id={project_row_id}）')
        else:
          logs.append(f'回滚：项目记录已删除（id={project_row_id}）')
      except Exception as ex:
        rollback_errors.append(f'回滚项目记录失败：{str(ex)}')

    if db_created and use_database and database_name:
      try:
        await _drop_database_if_exists(db_host, db_port, db_user, db_password, database_name)
        logs.append(f'回滚：数据库 {database_name} 已删除')
      except Exception as ex:
        rollback_errors.append(f'回滚数据库失败：{str(ex)}')

    if nginx_created and use_nginx and nginx_conf_path and nginx_server_row is not None:
      try:
        ok_rb, msg_rb = await _apply_nginx_conf_change_on_server(
          nginx_server_row,
          nginx_conf_path,
          lambda old: _remove_project_server_blocks(old, project_name)[0],
        )
        if ok_rb:
          logs.append(f'回滚：Nginx配置已删除 {project_name}')
        else:
          rollback_errors.append(f'回滚Nginx配置失败：{msg_rb}')
      except Exception as ex:
        rollback_errors.append(f'回滚Nginx配置失败：{str(ex)}')

    if conda_created:
      logs.append(f'$ conda env remove -n {conda_name} -y')
      code_rb, out_rb, err_rb = await _run_shell(conda_remove_cmd, timeout=3600)
      logs.extend(_split_lines(out_rb))
      logs.extend(_split_lines(err_rb))
      if code_rb != 0:
        rollback_errors.append(f'回滚Conda环境失败：{err_rb.strip() or out_rb.strip() or "unknown error"}')
      else:
        logs.append(f'回滚：Conda环境 {conda_name} 已删除')

    if frontend_dist_created and use_nginx:
      logs.append(f'frontend_dist base dir kept: {frontend_dist_base_dir}')

    if dir_created:
      logs.append(f'$ {rm_dir_cmd}')
      code_rb, out_rb, err_rb = await _run_shell(rm_dir_cmd, timeout=600)
      logs.extend(_split_lines(out_rb))
      logs.extend(_split_lines(err_rb))
      if code_rb != 0:
        rollback_errors.append(f'回滚项目目录失败：{err_rb.strip() or out_rb.strip() or "unknown error"}')
      else:
        logs.append(f'回滚：项目目录 {target_dir} 已删除')

    return rollback_errors

  try:
    logs.append(f'$ mkdir -p {shlex.quote(target_dir)}')
    code, out, err = await _run_shell(mkdir_cmd, timeout=60)
    if code != 0:
      actions.append(f'创建项目目录失败：{target_dir}')
      raise HTTPException(status_code=500, detail=f'创建项目目录失败：{err.strip() or "unknown error"}')
    dir_created = True
    actions.append(f'创建项目目录成功：{target_dir}')
    logs.append(f'创建项目目录成功：{target_dir}')

    if use_nginx:
      logs.append(f'$ mkdir -p {shlex.quote(frontend_dist_base_dir)}')
      code, out, err = await _run_shell(mkdir_frontend_dist_cmd, timeout=60)
      if code != 0:
        actions.append(f'创建前端打包目录失败：{frontend_dist_base_dir}')
        raise HTTPException(status_code=500, detail=f'create frontend_dist base dir failed: {frontend_dist_base_dir} {err.strip() or "unknown error"}'.strip())
      frontend_dist_created = True
      actions.append(f'创建前端打包目录成功：{frontend_dist_base_dir}')
      logs.append(f'创建前端打包目录成功：{frontend_dist_base_dir}')

    logs.append(f'开始创建Conda环境：{conda_name}，Python版本：{python_version}')
    code, out, err = await _run_shell(conda_cmd, timeout=3600)
    if code != 0:
      actions.append(f'创建Conda环境失败：{conda_name}，Python版本：{python_version}')
      raise HTTPException(status_code=500, detail=f'创建Conda环境失败：{err.strip() or "unknown error"}')
    conda_created = True
    actions.append(f'创建Conda环境成功：{conda_name}，Python版本：{python_version}')
    logs.append(f'创建Conda环境成功：{conda_name}，Python版本：{python_version}')

    logs.append(f'检查Conda环境Python版本：{conda_name}')
    code, out, err = await _run_shell(py_ver_cmd, timeout=120)
    if code != 0:
      actions.append(f'检查Python版本失败：{conda_name}')
      raise HTTPException(status_code=500, detail=f'Python版本验证失败：{err.strip() or "unknown error"}')
    python_check_text = ' '.join(_split_lines(out) + _split_lines(err)).strip() or python_version
    actions.append(f'检查Python版本成功：{python_check_text}')
    logs.append(f'检查Python版本成功：{python_check_text}')

    if use_database and database_name:
      mysql_ok, mysql_msg = await _check_server_mysql_connectable(db_host, db_port, db_user, db_password)
      if not mysql_ok:
        actions.append(f'创建数据库失败：{database_name}，MySQL不可用')
        raise HTTPException(status_code=500, detail=f'创建数据库失败，MySQL不可用：{mysql_msg}')
      try:
        await _create_database_utf8mb4(db_host, db_port, db_user, db_password, database_name)
      except Exception as ex:
        actions.append(f'创建数据库失败：{database_name}')
        raise HTTPException(status_code=500, detail=f'创建数据库失败：{str(ex)}')
      db_created = True
      actions.append(f'创建数据库成功：{database_name}（{db_host}:{db_port}）')
      logs.append(f'创建数据库成功：{database_name}（{db_host}:{db_port}）')

    if use_nginx:
      ok_nginx, msg_nginx = await _apply_nginx_conf_change_on_server(
        nginx_server_row,
        nginx_conf_path,
        lambda old: _replace_or_append_project_server_block(old, project_name, confirmed_nginx_config_text),
      )
      if not ok_nginx:
        actions.append(f'写入Nginx配置失败：{nginx_conf_path}')
        raise HTTPException(status_code=500, detail=f'创建Nginx配置失败：{msg_nginx}')
      nginx_created = True
      actions.append(f'写入Nginx配置成功：{nginx_conf_path}，listen={nginx_frontend_port}，proxy_pass={nginx_backend_port}')
      logs.append(f'写入Nginx配置成功：{nginx_conf_path}，listen={nginx_frontend_port}，proxy_pass={nginx_backend_port}')

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

    actions.append(f'创建项目记录成功：{project_name}')
    logs.append('创建成功')
    await record_project_operation(
      session,
      created,
      current_user,
      action='create',
      action_label='创建项目',
      summary=f'创建项目：{project_name}',
      before_data=None,
      after_data=snapshot_project_config(created, {'server_ip': str(server_row.ip or '')}),
      detail={'actions': actions},
    )
    return schemas.pspm.ProjectRealCreateResponseData(
      project_id=created.id,
      status='创建成功',
      backend_path=target_dir,
      conda_env_name=conda_name,
      python_version=python_version,
      logs=logs,
    )
  except HTTPException as ex:
    rollback_errors = await rollback_all()
    if rollback_errors:
      detail = f'{str(ex.detail)}；回滚异常：{" | ".join(rollback_errors)}'
      raise HTTPException(status_code=ex.status_code, detail=detail)
    raise
  except Exception as ex:
    rollback_errors = await rollback_all()
    detail = f'创建项目失败：{str(ex)}'
    if rollback_errors:
      detail = f'{detail}；回滚异常：{" | ".join(rollback_errors)}'
    raise HTTPException(status_code=500, detail=detail)
