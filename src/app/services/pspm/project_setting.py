"""项目设置服务模块，负责比较设置差异并执行 Conda、数据库、Nginx 等实际变更。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import shlex

from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_detail import build_changed_fields, record_project_operation, snapshot_project_config
from app.services.pspm.project_setting_helpers import (
  _changed_fields_for_update,
  _same_text,
  _text,
  build_setting_actions_from_changed_fields,
  normalize_project_setting_payload,
)
from app.services.pspm.project_helpers import (
  frontend_dist_base_dir_for_user,
  frontend_root_for_project,
  get_project_for_user,
  list_conda_env_names_on_server,
  safe_existing_conda_name,
)
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _create_database_utf8mb4,
  _drop_database_if_exists,
  _safe_db_host,
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
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.nginx_server_blocks import (
  _build_project_nginx_server_block,
  _normalize_confirmed_nginx_server_block,
  _remove_project_server_blocks,
  _replace_or_append_project_server_block,
)
from app.utils.pspm.path_utils import (
  _safe_conda_name,
  _safe_port_number,
  _safe_python_version,
)
from app.utils.pspm.conda_utils import run_conda_command_on_server
from app.utils.pspm.shell_utils import (
  _find_project_nginx_server_row,
  _find_server_row_by_id,
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _run_server_shell,
)




async def apply_conda_setting_change(project, project_server_row, data_in: dict, create_conda_env: bool, drop_original_conda_env: bool) -> list[str]:
  """按设置差异真实创建新 Conda 环境，或按用户选择删除原 Conda 环境。"""
  actions: list[str] = []
  original_conda_name = safe_existing_conda_name(getattr(project, 'conda_env_name', None))
  new_conda_name = _text(data_in.get('conda_env_name') if 'conda_env_name' in data_in else getattr(project, 'conda_env_name', ''))
  if new_conda_name:
    new_conda_name = _safe_conda_name(new_conda_name)
    data_in['conda_env_name'] = new_conda_name

  conda_changed = bool(new_conda_name and new_conda_name != original_conda_name)
  if create_conda_env and conda_changed:
    python_version = _text(data_in.get('python_version') or getattr(project, 'python_version', ''))
    python_version = _safe_python_version(python_version)
    data_in['python_version'] = python_version
    env_names = await list_conda_env_names_on_server(project_server_row)
    if new_conda_name in env_names:
      raise HTTPException(status_code=400, detail=f'Conda环境已存在：{new_conda_name}')
    conda_create_cmd = f'conda create -n {shlex.quote(new_conda_name)} python={shlex.quote(python_version)} -y'
    code, out, err = await run_conda_command_on_server(project_server_row, conda_create_cmd, timeout=3600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'创建Conda环境失败：{err.strip() or out.strip() or '未知错误'}')
    actions.append(f'创建Conda环境：{new_conda_name}，Python版本：{python_version}')

  if drop_original_conda_env and original_conda_name and conda_changed:
    conda_remove_cmd = f'conda env remove -n {shlex.quote(original_conda_name)} -y'
    code, out, err = await run_conda_command_on_server(project_server_row, conda_remove_cmd, timeout=3600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'删除原Conda环境失败：{original_conda_name} {err.strip() or out.strip() or '未知错误'}'.strip())
    actions.append(f'删除原Conda环境：{original_conda_name}')

  return actions


async def apply_nginx_setting_change(session, current_user, project, project_server_row, servers, data_in: dict, drop_original_nginx_config: bool) -> list[str]:
  """按设置差异真实写入、替换或删除 Nginx server block。"""
  actions: list[str] = []
  nginx_enabled = data_in.get('nginx_enabled') if 'nginx_enabled' in data_in else None
  project_name_text = _text(project.name)
  original_conf_path = _text(getattr(project, 'nginx_conf_path', ''))
  original_nginx_ip = _text(getattr(project, 'nginx_server_ip', ''))
  original_frontend_port = _text(getattr(project, 'frontend_port', ''))
  original_backend_port = _text(getattr(project, 'backend_deploy_port', ''))
  original_config_text = _text(getattr(project, 'nginx_config_text', ''))

  async def remove_original_block_if_needed(reason: str, skip_conf_path: str = ''):
    """按需删除原 Nginx 配置块。

    参数：
    - reason：删除原因，用于记录执行动作。
    - skip_conf_path：需要跳过删除的配置文件路径，通常是新旧配置文件相同时使用。

    作用：
    - 设置流程确认删除原 Nginx 配置时，只删除当前项目对应的 server block。
    """
    if not (drop_original_nginx_config and original_conf_path):
      return
    if skip_conf_path and original_conf_path == skip_conf_path:
      return
    original_server_row = _find_server_row_by_ip(servers, original_nginx_ip) if original_nginx_ip else _find_project_nginx_server_row(servers, project)
    if not original_server_row:
      raise HTTPException(status_code=403, detail='当前用户无原Nginx服务器使用权限，无法删除原配置')
    ok, msg = await _apply_nginx_conf_change_on_server(
      original_server_row,
      original_conf_path,
      lambda old: _remove_project_server_blocks(old, project_name_text)[0],
    )
    if not ok:
      raise HTTPException(status_code=500, detail=f'删除原Nginx配置失败：{msg}')
    actions.append(f'{reason}：{original_conf_path}')

  if nginx_enabled is True:
    nginx_server_ip = _text(data_in.get('nginx_server_ip') or getattr(project, 'nginx_server_ip', ''))
    nginx_conf_path = _text(data_in.get('nginx_conf_path') or getattr(project, 'nginx_conf_path', ''))
    frontend_port = _text(data_in.get('frontend_port') if 'frontend_port' in data_in else getattr(project, 'frontend_port', ''))
    backend_port = _text(data_in.get('backend_deploy_port') if 'backend_deploy_port' in data_in else getattr(project, 'backend_deploy_port', ''))

    if not frontend_port:
      raise HTTPException(status_code=400, detail='启用Nginx时必须填写Nginx前端端口')
    if not backend_port:
      raise HTTPException(status_code=400, detail='启用Nginx时必须填写后端部署端口')

    nginx_server_row = _find_server_row_by_ip(servers, nginx_server_ip) if nginx_server_ip else _find_project_nginx_server_row(servers, project)
    if not nginx_server_row:
      raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

    running = await _is_nginx_running_on_server(nginx_server_row)
    if not running:
      raise HTTPException(status_code=400, detail='nginx服务未开启')

    running_conf_path = await _get_running_nginx_conf_path_on_server(nginx_server_row)
    inventory = await _collect_nginx_conf_inventory_on_server(nginx_server_row, running_conf_path)
    conf_path = _validate_requested_nginx_conf_path(nginx_conf_path or running_conf_path, inventory)
    frontend_port_int = _safe_port_number(int(frontend_port))
    backend_port_int = _safe_port_number(int(backend_port))
    if frontend_port_int == backend_port_int:
      raise HTTPException(status_code=400, detail='Nginx前端端口和后端部署端口不能相同')

    submitted_block = _text(data_in.get('nginx_config_text'))
    new_block = _normalize_confirmed_nginx_server_block(submitted_block, project_name_text, frontend_port_int, backend_port_int) if submitted_block else ''
    target_tuple = (
      _text(getattr(nginx_server_row, 'ip', '') or nginx_server_ip),
      conf_path,
      str(frontend_port_int),
      str(backend_port_int),
      new_block,
    )
    original_tuple = (
      original_nginx_ip,
      original_conf_path,
      original_frontend_port,
      original_backend_port,
      original_config_text,
    )

    if target_tuple == original_tuple:
      data_in['nginx_conf_path'] = original_conf_path
      data_in['nginx_server_ip'] = original_nginx_ip
      data_in['frontend_port'] = original_frontend_port
      data_in['backend_deploy_port'] = original_backend_port
      data_in['nginx_config_text'] = original_config_text
      return actions

    # 同步已有项目时，绑定的是已经存在的 Nginx server block。
    # 这类原始 server block 可能没有系统标识，标准化配置文本会自动补充
    # `# pspm_project 项目名`，导致纯文本比较误判为“配置已修改”。
    # 如果 Nginx 服务器、配置文件路径、listen 端口、proxy_pass 端口都没有变化，
    # 且用户没有选择删除原配置，则说明本次设置没有实际变更 Nginx 绑定关系。
    # 此时必须跳过端口占用校验，否则同步进来的已有端口会被误判为被 Nginx 占用。
    same_nginx_binding = (
      _same_text(getattr(nginx_server_row, 'ip', '') or nginx_server_ip, original_nginx_ip)
      and _same_text(conf_path, original_conf_path)
      and _same_text(str(frontend_port_int), original_frontend_port)
      and _same_text(str(backend_port_int), original_backend_port)
    )
    if same_nginx_binding and not drop_original_nginx_config:
      data_in['nginx_conf_path'] = original_conf_path
      data_in['nginx_server_ip'] = original_nginx_ip
      data_in['frontend_port'] = original_frontend_port
      data_in['backend_deploy_port'] = original_backend_port
      data_in['nginx_config_text'] = original_config_text
      return actions

    frontend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, frontend_port_int, running_conf_path, project_name=project_name_text)
    if frontend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已在Nginx listen配置中占用')
    if frontend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已在Nginx proxy_pass配置中占用')

    backend_conflict = await _check_nginx_port_conflict_on_server(nginx_server_row, backend_port_int, running_conf_path, project_name=project_name_text)
    if backend_conflict.get('listen'):
      raise HTTPException(status_code=400, detail=f'后端部署端口 {backend_port_int} 已在Nginx listen配置中占用')
    if backend_conflict.get('proxy_pass'):
      raise HTTPException(status_code=400, detail=f'后端部署端口 {backend_port_int} 已在Nginx proxy_pass配置中占用')

    nginx_ip = _text(getattr(nginx_server_row, 'ip', '') or nginx_server_ip)
    backend_ip = _text(getattr(project_server_row, 'ip', '') or nginx_ip)
    is_root_user = await crud.rbac.is_root_user(session, user_id=current_user.id)
    frontend_dist_base_dir = frontend_dist_base_dir_for_user(current_user, is_root_user)
    frontend_project_root = frontend_root_for_project(current_user, is_root_user, project_name_text)
    code_fd, out_fd, err_fd = await _run_server_shell(nginx_server_row, f'mkdir -p {shlex.quote(frontend_dist_base_dir)}', timeout=60)
    if code_fd != 0:
      raise HTTPException(status_code=500, detail=f'创建前端打包目录失败：{frontend_dist_base_dir} {err_fd.strip() or out_fd.strip() or '未知错误'}'.strip())

    if not new_block:
      new_block = _build_project_nginx_server_block(
        project_name=project_name_text,
        frontend_port=frontend_port_int,
        backend_port=backend_port_int,
        backend_ip=backend_ip,
        nginx_server_ip=nginx_ip,
        username=_text(getattr(current_user, 'username', '') or 'root'),
        frontend_root=frontend_project_root,
      )

    ok, msg = await _apply_nginx_conf_change_on_server(
      nginx_server_row,
      conf_path,
      lambda old: _replace_or_append_project_server_block(old, project_name_text, new_block),
    )
    if not ok:
      raise HTTPException(status_code=500, detail=msg)
    actions.append(f'写入Nginx配置：{conf_path}，listen={frontend_port_int}，proxy_pass={backend_port_int}')
    await remove_original_block_if_needed('删除原Nginx配置', skip_conf_path=conf_path)

    data_in['nginx_conf_path'] = conf_path
    data_in['nginx_server_ip'] = nginx_ip
    data_in['frontend_path'] = frontend_project_root
    data_in['frontend_port'] = str(frontend_port_int)
    data_in['backend_deploy_port'] = str(backend_port_int)
    data_in['nginx_config_text'] = new_block
  elif nginx_enabled is False:
    await remove_original_block_if_needed('删除原Nginx配置')
    data_in['nginx_conf_path'] = ''
    data_in['nginx_server_ip'] = ''
    data_in['frontend_port'] = ''
    data_in['backend_deploy_port'] = ''
    data_in['nginx_config_text'] = ''

  return actions


async def apply_database_setting_change(project, data_in: dict, drop_original_database: bool) -> list[str]:
  """按设置差异真实创建新数据库，或按用户选择删除原数据库。"""
  actions: list[str] = []
  database_keys = {'database_name', 'database_host', 'database_port', 'database_user', 'database_password'}
  if not database_keys.intersection(data_in.keys()):
    return actions

  original_name = _safe_optional_db_name(_text(getattr(project, 'database_name', '')))
  original_host = _text(getattr(project, 'database_host', ''))
  original_port = _text(getattr(project, 'database_port', ''))
  original_user = _text(getattr(project, 'database_user', ''))
  original_password = str(getattr(project, 'database_password', '') or '')

  desired_name = _safe_optional_db_name(_text(data_in.get('database_name')))
  if not desired_name:
    if drop_original_database and original_name:
      await _drop_database_if_exists(
        _safe_db_host(original_host),
        _safe_db_port(int(original_port)),
        _safe_db_user(original_user),
        original_password,
        original_name,
      )
      actions.append(f'删除原数据库：{original_name}')
    data_in['database_name'] = ''
    data_in['database_host'] = ''
    data_in['database_port'] = ''
    data_in['database_user'] = ''
    data_in['database_password'] = ''
    return actions

  desired_host = _safe_db_host(_text(data_in.get('database_host')))
  desired_port = str(_safe_db_port(int(_text(data_in.get('database_port')))))
  desired_user = _safe_db_user(_text(data_in.get('database_user')))
  desired_password = str(data_in.get('database_password') or '')
  data_in['database_name'] = desired_name
  data_in['database_host'] = desired_host
  data_in['database_port'] = desired_port
  data_in['database_user'] = desired_user
  data_in['database_password'] = desired_password

  original_target = (original_name, original_host, original_port)
  desired_target = (desired_name, desired_host, desired_port)
  database_target_changed = desired_target != original_target

  if database_target_changed:
    mysql_ok, _mysql_msg = await _check_server_mysql_connectable(desired_host, int(desired_port), desired_user, desired_password)
    if not mysql_ok:
      raise HTTPException(status_code=400, detail='数据库连接失败')
    exists = await _check_database_exists(desired_host, int(desired_port), desired_user, desired_password, desired_name)
    if exists:
      raise HTTPException(status_code=400, detail=f'数据库 {desired_name} 已经存在，不可创建')
    await _create_database_utf8mb4(desired_host, int(desired_port), desired_user, desired_password, desired_name)
    actions.append(f'创建数据库：{desired_name}（{desired_host}:{desired_port}）')

  if drop_original_database and original_name and database_target_changed:
    await _drop_database_if_exists(
      _safe_db_host(original_host),
      _safe_db_port(int(original_port)),
      _safe_db_user(original_user),
      original_password,
      original_name,
    )
    actions.append(f'删除原数据库：{original_name}')

  return actions


async def update_project_setting_service(session, current_user, project_id: int, payload: schemas.pspm.ProjectSettingUpdate):
  """保存项目设置，并只对和原配置不同的部分执行真实操作。"""
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  before_snapshot = snapshot_project_config(project)
  data_in, drop_original_database, create_conda_env, drop_original_conda_env, drop_original_nginx_config = normalize_project_setting_payload(payload, project)

  servers = await _list_allowed_server_rows(session, current_user)
  project_server_row = _find_server_row_by_id(servers, getattr(project, 'server_id', None))
  if not project_server_row:
    raise HTTPException(status_code=403, detail='当前用户无该项目服务器使用权限')

  actions: list[str] = []
  actions.extend(await apply_conda_setting_change(project, project_server_row, data_in, create_conda_env, drop_original_conda_env))
  actions.extend(await apply_database_setting_change(project, data_in, drop_original_database))
  actions.extend(await apply_nginx_setting_change(session, current_user, project, project_server_row, servers, data_in, drop_original_nginx_config))

  changed_data = _changed_fields_for_update(project, data_in)
  if changed_data:
    rows = await crud.projects.update(session, obj_in={'id': project_id}, data_in=changed_data)
    if not rows:
      raise HTTPException(status_code=400, detail='更新失败')
    await session.refresh(project)

  after_snapshot = snapshot_project_config(project)
  changed_fields = build_changed_fields(before_snapshot, after_snapshot)
  action_rows = build_setting_actions_from_changed_fields(changed_fields) + actions
  if changed_fields or action_rows:
    await record_project_operation(
      session,
      project,
      current_user,
      action='setting',
      action_label='修改项目设置',
      summary=f'修改项目设置：{project.name}',
      before_data=before_snapshot,
      after_data=after_snapshot,
      detail={
        'changed_fields': changed_fields,
        'actions': action_rows,
      },
    )

  return {
    'changed_fields': changed_fields,
    'actions': action_rows,
  }

async def delete_original_project_database_service(session, current_user, project_id: int):
  """删除项目当前记录的原数据库，并同步清空项目表中的数据库配置字段。

  参数：
  - session：当前 FastAPI 请求注入的异步数据库会话，用于读取项目、更新项目字段和写入操作日志。
  - current_user：当前登录用户对象，用于权限范围校验和操作人记录。
  - project_id：项目 ID，来自 `/api/pspm/projects/database/original` 接口的 Query 参数。

  作用：
  - 兼容项目接口层的独立删除原数据库入口。
  - 只删除当前项目记录里配置的数据库，不会影响其他项目数据库。
  - 删除成功后把项目表中的 database_name/database_host/database_port/database_user/database_password 清空为空字符串。

  返回：
  - 无显式返回值；异常时抛出 HTTPException，由 FastAPI 转换成接口错误响应。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  before_snapshot = snapshot_project_config(project)

  database_name = _safe_optional_db_name(_text(getattr(project, 'database_name', '')))
  database_host = _text(getattr(project, 'database_host', ''))
  database_port = _text(getattr(project, 'database_port', ''))
  database_user = _text(getattr(project, 'database_user', ''))
  database_password = str(getattr(project, 'database_password', '') or '')

  if not database_name:
    return
  if not (database_host and database_port and database_user):
    raise HTTPException(status_code=400, detail='数据库连接信息不完整，无法删除原数据库')

  await _drop_database_if_exists(
    _safe_db_host(database_host),
    _safe_db_port(int(database_port)),
    _safe_db_user(database_user),
    database_password,
    database_name,
  )

  clear_data = {
    'database_name': '',
    'database_host': '',
    'database_port': '',
    'database_user': '',
    'database_password': '',
  }
  rows = await crud.projects.update(session, obj_in={'id': project_id}, data_in=clear_data)
  if not rows:
    raise HTTPException(status_code=400, detail='清空数据库配置失败')
  await session.refresh(project)

  after_snapshot = snapshot_project_config(project)
  changed_fields = build_changed_fields(before_snapshot, after_snapshot)
  await record_project_operation(
    session,
    project,
    current_user,
    action='setting',
    action_label='删除原数据库',
    summary=f'删除原数据库：{project.name}',
    before_data=before_snapshot,
    after_data=after_snapshot,
    detail={
      'changed_fields': changed_fields,
      'actions': [f'删除原数据库：{database_name}'],
    },
  )
