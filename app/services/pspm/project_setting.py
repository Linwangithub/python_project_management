import shlex

from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_detail import build_changed_fields, record_project_operation, snapshot_project_config
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
  _build_project_nginx_server_block,
  _check_nginx_port_conflict_on_server,
  _collect_nginx_conf_inventory_on_server,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _normalize_confirmed_nginx_server_block,
  _remove_project_server_blocks,
  _replace_or_append_project_server_block,
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.path_utils import (
  _safe_conda_name,
  _safe_entry_file_path,
  _safe_optional_port_text,
  _safe_port_number,
  _safe_python_version,
)
from app.utils.pspm.project_config import CONDA_INIT
from app.utils.pspm.shell_utils import (
  _find_project_nginx_server_row,
  _find_server_row_by_id,
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _run_server_shell,
)


def _text(value) -> str:
  """把配置值统一转换成去掉首尾空白的字符串。"""
  return str(value or '').strip()


def _same_text(left, right) -> bool:
  """判断两个配置值是否一致；None 和空字符串都视为空配置。"""
  return _text(left) == _text(right)


def _changed_fields_for_update(project, data_in: dict) -> dict:
  """只保留和项目原配置不一致的字段，避免后端重复更新。"""
  changed: dict = {}
  for key, value in data_in.items():
    if key == 'nginx_enabled':
      continue
    if not hasattr(project, key):
      continue
    if _same_text(getattr(project, key, ''), value):
      continue
    changed[key] = value
  return changed


SETTING_ACTION_FIELD_LABELS = {
  'description': '项目描述',
  'conda_env_name': 'Conda环境',
  'python_version': 'Python版本',
  'entry_file_path': '项目入口文件位置',
  'backend_dev_port': '后端开发端口',
  'backend_deploy_port': '后端部署端口',
  'frontend_port': 'Nginx前端端口',
  'dev_start_command': '开发启动命令',
  'deploy_start_command': '部署启动命令',
  'database_name': '数据库名称',
  'database_host': '数据库IP',
  'database_port': '数据库端口',
  'database_user': '数据库账号',
  'database_password': '数据库密码',
  'nginx_server_ip': 'Nginx服务器IP',
  'nginx_conf_path': 'Nginx配置文件路径',
  'nginx_config_text': 'Nginx详细配置',
  'frontend_path': '前端打包文件位置',
}


def _format_action_value(value) -> str:
  """把配置变更值转换成适合写入 actions 的短文本。"""
  text = _text(value)
  if not text:
    return '空'
  if len(text) > 180:
    return f'{text[:180]}...'
  return text


def build_setting_actions_from_changed_fields(changed_fields: list[dict]) -> list[str]:
  """把普通字段差异补充到日志 actions，方便日志弹框直接展示完整动作。"""
  actions: list[str] = []
  for item in changed_fields or []:
    key = item.get('key')
    if key in {'id', 'owner_id', 'server_id', 'status', 'auto_start', 'created_at', 'updated_at'}:
      continue
    label = SETTING_ACTION_FIELD_LABELS.get(key, item.get('label') or key)
    before = _format_action_value(item.get('before'))
    after = _format_action_value(item.get('after'))
    actions.append(f'修改{label}：{before} -> {after}')
  return actions


def normalize_project_setting_payload(payload: schemas.pspm.ProjectSettingUpdate, project) -> tuple[dict, bool, bool, bool, bool]:
  """整理项目设置请求体，生成可写入项目表的字段字典。"""
  drop_original_database = bool(getattr(payload, 'drop_original_database', False))
  create_conda_env = bool(getattr(payload, 'create_conda_env', False))
  drop_original_conda_env = bool(getattr(payload, 'drop_original_conda_env', False))
  drop_original_nginx_config = bool(getattr(payload, 'drop_original_nginx_config', False))

  data_in = payload.model_dump(exclude_none=True)
  data_in.pop('drop_original_database', None)
  data_in.pop('create_conda_env', None)
  data_in.pop('drop_original_conda_env', None)
  data_in.pop('drop_original_nginx_config', None)

  if 'description' in data_in:
    data_in['description'] = _text(data_in.get('description'))
  if 'conda_env_name' in data_in:
    conda_env_value = _text(data_in.get('conda_env_name'))
    data_in['conda_env_name'] = _safe_conda_name(conda_env_value) if conda_env_value else ''
  if 'python_version' in data_in:
    python_version_value = _text(data_in.get('python_version'))
    data_in['python_version'] = _safe_python_version(python_version_value) if python_version_value else ''
  if 'entry_file_path' in data_in:
    data_in['entry_file_path'] = _safe_entry_file_path(data_in.get('entry_file_path') or '')
  if 'backend_dev_port' in data_in:
    data_in['backend_dev_port'] = _safe_optional_port_text(data_in.get('backend_dev_port'))
  if 'backend_deploy_port' in data_in:
    data_in['backend_deploy_port'] = _safe_optional_port_text(data_in.get('backend_deploy_port'))
  if 'frontend_port' in data_in:
    data_in['frontend_port'] = _safe_optional_port_text(data_in.get('frontend_port'))

  database_keys = {'database_name', 'database_host', 'database_port', 'database_user', 'database_password'}
  if database_keys.intersection(data_in.keys()):
    db_name_value = _safe_optional_db_name(_text(data_in.get('database_name'))) if 'database_name' in data_in else _safe_optional_db_name(_text(getattr(project, 'database_name', '')))
    if not db_name_value:
      data_in['database_name'] = ''
      data_in['database_host'] = ''
      data_in['database_port'] = ''
      data_in['database_user'] = ''
      data_in['database_password'] = ''
    else:
      data_in['database_name'] = db_name_value
      if 'database_host' in data_in:
        host_value = _text(data_in.get('database_host'))
        data_in['database_host'] = _safe_db_host(host_value) if host_value else ''
      if 'database_port' in data_in:
        port_value = _text(data_in.get('database_port'))
        data_in['database_port'] = str(_safe_db_port(int(port_value))) if port_value else ''
      if 'database_user' in data_in:
        user_value = _text(data_in.get('database_user'))
        data_in['database_user'] = _safe_db_user(user_value) if user_value else ''
      if 'database_password' in data_in:
        data_in['database_password'] = str(data_in.get('database_password') or '')

  return data_in, drop_original_database, create_conda_env, drop_original_conda_env, drop_original_nginx_config


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
    conda_create_cmd = f'{CONDA_INIT}; conda create -n {shlex.quote(new_conda_name)} python={shlex.quote(python_version)} -y'
    code, out, err = await _run_server_shell(project_server_row, conda_create_cmd, timeout=3600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'创建Conda环境失败：{err.strip() or out.strip() or "unknown error"}')
    actions.append(f'创建Conda环境：{new_conda_name}，Python版本：{python_version}')

  if drop_original_conda_env and original_conda_name and conda_changed:
    conda_remove_cmd = f'{CONDA_INIT}; conda env remove -n {shlex.quote(original_conda_name)} -y'
    code, out, err = await _run_server_shell(project_server_row, conda_remove_cmd, timeout=3600)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'删除原Conda环境失败：{original_conda_name} {err.strip() or out.strip() or "unknown error"}'.strip())
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
      raise HTTPException(status_code=500, detail=f'创建前端打包目录失败：{frontend_dist_base_dir} {err_fd.strip() or out_fd.strip() or "unknown error"}'.strip())

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

