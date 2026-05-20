import os
import shlex
from typing import Any

from fastapi import HTTPException

from app import crud, schemas
from app.core.deps import get_settings
from app.services.pspm.project_detail import record_project_operation, snapshot_project_config
from app.services.pspm.project_helpers import (
  frontend_root_for_project,
  list_conda_env_names_on_server,
  parse_conda_envs_dir,
)
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _list_database_names,
  _safe_db_host,
  _safe_db_identifier,
  _safe_optional_db_name,
  _safe_db_port,
  _safe_db_user,
)
from app.utils.pspm.nginx_utils import (
  _check_nginx_port_conflict_on_server,
  _collect_nginx_conf_inventory_on_server,
  _find_server_block_ranges,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _is_port_in_use_on_server,
  _read_text_on_server,
  _server_block_listen_ports,
  _server_block_proxy_pass_ports,
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.path_utils import (
  _normalize_path,
  _safe_conda_name,
  _safe_optional_port_text,
  _safe_project_name,
  _safe_rel_path_input,
)
from app.utils.pspm.project_config import CONDA_INIT
from app.utils.pspm.shell_utils import (
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _ping_from_server_to_target,
  _run_server_shell,
  _split_lines,
)


def _project_base_path_for_user(current_user, is_root: bool) -> str:
  """返回当前用户同步已有项目时允许浏览的项目根目录。

  参数：
  - current_user：当前登录用户。
  - is_root：当前用户是否为 root 角色。

  作用：
  - 同步已有项目只能在配置文件定义的项目目录前缀下选择目录。
  - root 使用 `/root`，普通用户使用 `/home/{username}`。

  返回：
  - 绝对路径字符串。
  """
  username = str(getattr(current_user, 'username', '') or 'user').strip() or 'user'
  if is_root:
    return '/root'
  return _normalize_path(f'/home/{username}')


def _clean_python_version_output(text: str) -> str:
  """清洗 Conda Python 版本输出。

  参数：
  - text：`conda run -n xxx python --version` 的 stdout/stderr 合并文本。

  作用：
  - SSH 首次连接远程服务器时，stderr 里可能包含 `Warning: Permanently added ...`。
  - 前端只需要展示实际 Python 版本，不应该把 SSH warning 混进去。

  返回：
  - 形如 `Python 3.8.13` 的版本文本；未匹配到时返回原始非 warning 文本。
  """
  lines = [x.strip() for x in _split_lines(text) if x.strip()]
  for line in lines:
    if line.lower().startswith('python '):
      return line
  filtered = [line for line in lines if not line.lower().startswith('warning: permanently added')]
  return ' '.join(filtered).strip()


def _safe_sync_abs_path(base_path: str, rel_path: str) -> str:
  """把同步弹框传入的相对目录解析为安全绝对路径。

  参数：
  - base_path：允许的项目根目录。
  - rel_path：前端级联目录相对路径。

  作用：
  - 防止通过 `..` 或绝对路径越过项目根目录。

  返回：
  - 解析后的绝对路径。
  """
  base = _normalize_path(base_path)
  rel = _safe_rel_path_input(rel_path)
  if not rel:
    return base
  target = os.path.normpath(os.path.join(base, rel))
  if target != base and not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail='项目目录越界')
  return target


def _safe_sync_backend_path(base_path: str, backend_path: str) -> str:
  """校验同步项目目录必须存在于允许前缀下。"""
  base = _normalize_path(base_path)
  target = _normalize_path(backend_path)
  if target == base:
    raise HTTPException(status_code=400, detail='请选择具体项目目录，不能选择项目根目录')
  if not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail=f'项目目录必须位于 {base} 下')
  return target


def _safe_sync_entry_file_path(backend_path: str, entry_file_path: str) -> str:
  """校验同步已有项目时选择的入口文件必须位于项目目录内。

  参数：
  - backend_path：已经选择的项目目录绝对路径。
  - entry_file_path：前端提交的入口文件绝对路径，可以为空。

  作用：
  - 同步已有项目时允许一起登记入口文件。
  - 如果用户选择了入口文件，则必须保证它没有越过项目目录。

  返回：
  - 空字符串，或标准化后的入口文件绝对路径。
  """
  value = str(entry_file_path or '').strip()
  if not value:
    return ''
  base = _normalize_path(backend_path)
  target = _normalize_path(value)
  if target == base or not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail=f'入口文件必须位于项目目录 {base} 下')
  return target


async def _get_allowed_server_by_ip(session, current_user, server_ip: str):
  """按 IP 查询当前用户可使用的服务器记录。"""
  ip = str(server_ip or '').strip()
  if not ip:
    raise HTTPException(status_code=400, detail='服务器IP不能为空')
  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_ip(servers, ip)
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')
  return servers, server_row


async def _server_directory_exists(server_row, path: str) -> bool:
  """检查指定服务器目录是否存在。"""
  code, _out, _err = await _run_server_shell(server_row, f'test -d {shlex.quote(path)}', timeout=15)
  return code == 0


async def _server_file_exists(server_row, path: str) -> bool:
  """检查指定服务器文件是否存在。"""
  code, _out, _err = await _run_server_shell(server_row, f'test -f {shlex.quote(path)}', timeout=15)
  return code == 0


async def _list_directory_children(server_row, path: str) -> list[str]:
  """列出指定服务器目录下的一层子目录名称。"""
  command = f'if [ -d {shlex.quote(path)} ]; then find {shlex.quote(path)} -mindepth 1 -maxdepth 1 -type d ! -name ".*" -printf "%f\\n" | sort; fi'
  code, out, err = await _run_server_shell(server_row, command, timeout=60)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'读取项目目录失败：{err.strip() or out.strip() or "unknown error"}')
  return [x.strip() for x in _split_lines(out) if x.strip()]


async def _list_entry_children(server_row, path: str, rel_dir: str) -> list[schemas.pspm.ProjectEntryPathNode]:
  """列出同步入口文件选择器的一层目录和文件。

  参数：
  - server_row：服务器记录，包含连接目标和凭据。
  - path：本次要读取的绝对目录。
  - rel_dir：当前目录相对项目目录的路径。

  返回：
  - 目录和文件节点列表，目录 `leaf=False`，文件 `leaf=True`。
  """
  command = (
    f'if [ -d {shlex.quote(path)} ]; then '
    f'find {shlex.quote(path)} -mindepth 1 -maxdepth 1 '
    f'\\( -type d -printf "d\\t%f\\n" -o -type f -printf "f\\t%f\\n" \\) | sort -k1,1 -k2,2; '
    f'else exit 2; fi'
  )
  code, out, err = await _run_server_shell(server_row, command, timeout=60)
  if code == 2:
    raise HTTPException(status_code=400, detail='入口文件目录不存在')
  if code != 0:
    raise HTTPException(status_code=500, detail=f'读取入口文件目录失败：{err.strip() or out.strip() or "unknown error"}')

  rel_base = _safe_rel_path_input(rel_dir)
  nodes: list[schemas.pspm.ProjectEntryPathNode] = []
  for line in _split_lines(out):
    text = line.strip()
    if not text or '\t' not in text:
      continue
    kind, name = text.split('\t', 1)
    name = name.strip()
    if not name or name in {'.', '..'} or name.startswith('.'):
      continue
    child_rel = name if not rel_base else f'{rel_base}/{name}'
    if kind == 'd':
      nodes.append(schemas.pspm.ProjectEntryPathNode(label=f'{name}/', value=child_rel, leaf=False))
    elif kind == 'f':
      nodes.append(schemas.pspm.ProjectEntryPathNode(label=name, value=child_rel, leaf=True))
  return nodes


async def _query_conda_env_detail(server_row, env_name: str) -> tuple[str, str]:
  """查询指定 Conda 环境的实际路径和 Python 版本。"""
  name = _safe_conda_name(env_name)
  code, out, err = await _run_server_shell(server_row, f'{CONDA_INIT}; conda info', timeout=120)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or "unknown error"}')

  envs_dir = parse_conda_envs_dir(out)
  if not envs_dir:
    raise HTTPException(status_code=500, detail='未解析到Conda环境目录')

  envs = await list_conda_env_names_on_server(server_row)
  if name not in envs:
    raise HTTPException(status_code=400, detail=f'Conda环境不存在：{name}')

  env_path = f'{envs_dir.rstrip("/")}/{name}'
  code_py, out_py, err_py = await _run_server_shell(
    server_row,
    f'{CONDA_INIT}; conda run -n {shlex.quote(name)} python --version',
    timeout=120,
  )
  if code_py != 0:
    raise HTTPException(status_code=400, detail=f'Conda环境不可用：{err_py.strip() or out_py.strip() or "无法获取Python版本"}')
  python_version = _clean_python_version_output('\n'.join(_split_lines(out_py) + _split_lines(err_py)))
  return env_path, python_version


async def list_sync_project_path_children_service(session, current_user, payload: schemas.pspm.ProjectSyncPathChildrenRequest):
  """查询同步已有项目时项目目录选择器的一层子目录。"""
  servers, server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  _ = servers
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  base_path = _project_base_path_for_user(current_user, is_root)
  target_dir = _safe_sync_abs_path(base_path, payload.rel_path)
  if not await _server_directory_exists(server_row, target_dir):
    raise HTTPException(status_code=400, detail=f'项目目录不存在：{target_dir}')

  nodes: list[schemas.pspm.ProjectSyncPathNode] = []
  for name in await _list_directory_children(server_row, target_dir):
    rel = name if not str(payload.rel_path or '').strip() else f'{_safe_rel_path_input(payload.rel_path)}/{name}'
    abs_path = os.path.normpath(os.path.join(base_path, rel))
    nodes.append(schemas.pspm.ProjectSyncPathNode(
      label=f'{name}/',
      value=rel,
      abs_path=abs_path,
      leaf=False,
    ))
  return nodes


async def list_sync_entry_path_children_service(session, current_user, payload: schemas.pspm.ProjectSyncEntryPathChildrenRequest):
  """查询同步已有项目时入口文件选择器的一层目录和文件。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload：服务器 IP、已选择项目目录、当前相对目录。

  作用：
  - 前端同步弹框选择项目目录后，使用该接口在该目录内继续选择入口文件。
  - 后端再次校验项目目录必须位于允许前缀下，避免越权浏览服务器文件。
  """
  _servers, server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  base_path = _project_base_path_for_user(current_user, is_root)
  backend_path = _safe_sync_backend_path(base_path, payload.backend_path)
  rel_path = _safe_rel_path_input(payload.rel_path)
  target_dir = backend_path if not rel_path else os.path.normpath(os.path.join(backend_path, rel_path))
  if target_dir != backend_path and not target_dir.startswith(f'{backend_path}/'):
    raise HTTPException(status_code=400, detail='入口文件路径越界')
  return await _list_entry_children(server_row, target_dir, rel_path)


async def list_sync_conda_envs_service(session, current_user, payload: schemas.pspm.ProjectSyncCondaEnvListRequest):
  """查询同步已有项目可选择的 Conda 环境列表。"""
  _servers, server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  code, out, err = await _run_server_shell(server_row, f'{CONDA_INIT}; conda info', timeout=120)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or "unknown error"}')
  envs_dir = parse_conda_envs_dir(out)
  if not envs_dir:
    raise HTTPException(status_code=500, detail='未解析到Conda环境目录')
  envs = await list_conda_env_names_on_server(server_row)
  return schemas.pspm.ProjectSyncCondaEnvListData(envs_dir=envs_dir, envs=envs)


async def check_sync_conda_service(session, current_user, payload: schemas.pspm.ProjectSyncCondaCheckRequest):
  """检查同步已有项目选择的 Conda 环境是否存在并回显 Python 版本。"""
  _servers, server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  env_path, python_version = await _query_conda_env_detail(server_row, payload.conda_env_name)
  return schemas.pspm.ProjectSyncCondaCheckData(
    ok=True,
    env_name=_safe_conda_name(payload.conda_env_name),
    env_path=env_path,
    python_version=python_version,
    message='Conda环境可用',
  )


async def check_sync_database_service(payload: schemas.pspm.ProjectSyncDatabaseCheckRequest):
  """检查同步已有项目绑定的数据库连接可用，并返回可选择的数据库列表。"""
  host = _safe_db_host(payload.host)
  port = _safe_db_port(payload.port)
  username = _safe_db_user(payload.username)
  password = str(payload.password or '')
  database_name = _safe_optional_db_name(payload.database_name)

  ok, _message = await _check_server_mysql_connectable(host, port, username, password)
  if not ok:
    raise HTTPException(status_code=400, detail='连接失败')

  databases = await _list_database_names(host, port, username, password)
  database_exists = False
  if database_name:
    database_exists = await _check_database_exists(host, port, username, password, database_name)
    if not database_exists:
      raise HTTPException(status_code=400, detail='连接成功，但该数据库不存在，不可同步')

  return schemas.pspm.ProjectSyncDatabaseCheckData(
    ok=True,
    message='连接成功，请选择要同步的数据库',
    server_mysql_ok=True,
    database_exists=database_exists,
    can_create=False,
    databases=databases,
  )


def _find_matching_nginx_server_block(conf_text: str, frontend_port: int, backend_port: int) -> str:
  """从 Nginx 配置文本中提取匹配指定前后端端口的 server 块。

  参数：
  - conf_text：Nginx 配置文件完整文本。
  - frontend_port：页面填写的 Nginx 前端 listen 端口。
  - backend_port：页面填写的后端部署端口，也就是 proxy_pass 指向端口。

  作用：
  - 同步已有项目时，用户只选择已有 Nginx 配置文件和端口。
  - 后端自动找到对应 server 块并保存为项目配置快照。

  返回：
  - 匹配到的 server 块文本；找不到返回空字符串。
  """
  text = str(conf_text or '')
  for start, end in _find_server_block_ranges(text):
    block = text[start:end].strip()
    if frontend_port in _server_block_listen_ports(block) and backend_port in _server_block_proxy_pass_ports(block):
      return block if block.endswith('\n') else f'{block}\n'
  return ''


async def _validate_sync_nginx_config(
  *,
  servers,
  project_server_row,
  project_name: str,
  server_ip: str,
  nginx_server_ip: str,
  requested_conf_path: str,
  frontend_port: str,
  backend_deploy_port: str,
  nginx_config_text: str,
) -> dict[str, str]:
  """校验同步已有项目绑定的 Nginx 配置。"""
  nginx_ip = str(nginx_server_ip or server_ip).strip()
  nginx_server_row = _find_server_row_by_ip(servers, nginx_ip)
  if not nginx_server_row:
    raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

  ping_ok, ping_msg = await _ping_from_server_to_target(project_server_row, nginx_ip)
  if not ping_ok:
    raise HTTPException(status_code=400, detail=f'Nginx服务器不可达：{ping_msg}')

  running = await _is_nginx_running_on_server(nginx_server_row)
  if not running:
    raise HTTPException(status_code=400, detail='nginx服务未开启')

  running_conf_path = await _get_running_nginx_conf_path_on_server(nginx_server_row)
  inventory = await _collect_nginx_conf_inventory_on_server(nginx_server_row, running_conf_path)
  nginx_conf_path = _validate_requested_nginx_conf_path(requested_conf_path, inventory)
  if not await _server_file_exists(nginx_server_row, nginx_conf_path):
    raise HTTPException(status_code=400, detail=f'Nginx配置文件不存在：{nginx_conf_path}')

  nginx_frontend_port = _safe_optional_port_text(frontend_port)
  nginx_backend_port = _safe_optional_port_text(backend_deploy_port)
  if not nginx_frontend_port or not nginx_backend_port:
    raise HTTPException(status_code=400, detail='启用Nginx时必须填写前端端口和后端部署端口')
  if server_ip == nginx_ip and nginx_frontend_port == nginx_backend_port:
    raise HTTPException(status_code=400, detail='服务器IP和Nginx服务器IP相同时，Nginx前端端口和后端部署端口不能相同')

  frontend_port_int = int(nginx_frontend_port)
  backend_port_int = int(nginx_backend_port)
  if await _is_port_in_use_on_server(nginx_server_row, frontend_port_int):
    raise HTTPException(status_code=400, detail=f'Nginx前端端口 {frontend_port_int} 已被系统占用')

  # 同步已有项目只绑定已存在 Nginx 配置，不要求前端提交详细配置文本。
  # 后端读取用户选择的配置文件，自动提取同时包含 listen 前端端口和 proxy_pass 后端端口的 server 块。
  ok, file_text = await _read_text_on_server(nginx_server_row, nginx_conf_path)
  if not ok:
    raise HTTPException(status_code=400, detail=f'读取Nginx配置失败：{file_text}')
  config_text = _find_matching_nginx_server_block(file_text, frontend_port_int, backend_port_int)
  if not config_text:
    raise HTTPException(
      status_code=400,
      detail=f'所选Nginx配置文件中未找到 listen {frontend_port_int} 且 proxy_pass 指向端口 {backend_port_int} 的 server 块',
    )

  return {
    'nginx_server_ip': nginx_ip,
    'nginx_conf_path': nginx_conf_path,
    'frontend_port': nginx_frontend_port,
    'backend_deploy_port': nginx_backend_port,
    'nginx_config_text': config_text,
  }


async def check_sync_nginx_server_block_service(session, current_user, payload: schemas.pspm.ProjectSyncNginxServerBlockCheckRequest):
  """检查同步已有项目填写的 Nginx 前后端端口是否能匹配已有 server 块。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload：项目服务器 IP、Nginx 服务器 IP、配置文件路径、前端端口、后端部署端口。

  作用：
  - 前端在 Nginx 前端端口和后端部署端口失焦时调用。
  - 提前告诉用户所选配置文件里是否存在对应 server 块，不等到最终同步才报错。
  """
  servers, project_server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  data = await _validate_sync_nginx_config(
    servers=servers,
    project_server_row=project_server_row,
    project_name='sync_preview',
    server_ip=str(payload.server_ip or '').strip(),
    nginx_server_ip=payload.nginx_server_ip,
    requested_conf_path=payload.nginx_conf_path,
    frontend_port=payload.frontend_port,
    backend_deploy_port=payload.backend_deploy_port,
    nginx_config_text='',
  )
  return schemas.pspm.ProjectSyncNginxServerBlockCheckData(
    ok=True,
    nginx_config_text=data.get('nginx_config_text') or '',
    message='已找到匹配的Nginx server块',
  )


async def sync_existing_project_service(session, current_user, payload: schemas.pspm.ProjectSyncRequest):
  """把已经存在的项目目录、Conda、数据库和 Nginx 配置同步登记到系统。"""
  project_name = _safe_project_name(payload.name)
  server_ip = str(payload.server_ip or '').strip()
  servers, server_row = await _get_allowed_server_by_ip(session, current_user, server_ip)
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  base_path = _project_base_path_for_user(current_user, is_root)
  backend_path = _safe_sync_backend_path(base_path, payload.backend_path)

  if not await _server_directory_exists(server_row, backend_path):
    raise HTTPException(status_code=400, detail=f'项目目录不存在：{backend_path}')

  entry_file_path = _safe_sync_entry_file_path(backend_path, payload.entry_file_path)
  if entry_file_path and not await _server_file_exists(server_row, entry_file_path):
    raise HTTPException(status_code=400, detail=f'入口文件不存在：{entry_file_path}')

  exists_db = await crud.projects.get(session, obj_in={'owner_id': current_user.id, 'name': project_name, 'status': [0, 1]})
  if exists_db:
    raise HTTPException(status_code=400, detail='项目名称已存在')

  conda_name = _safe_conda_name(payload.conda_env_name)
  conda_path, actual_python_version = await _query_conda_env_detail(server_row, conda_name)
  python_version = str(payload.python_version or actual_python_version or '').strip()

  use_database = bool(payload.use_database)
  database_name = ''
  db_host = ''
  db_port: int | None = None
  db_user = ''
  db_password = ''
  if use_database:
    database_name = _safe_db_identifier(payload.database_name)
    db_host = _safe_db_host(payload.database_host)
    db_port = _safe_db_port(payload.database_port)
    db_user = _safe_db_user(payload.database_user)
    db_password = str(payload.database_password or '')
    ok, _message = await _check_server_mysql_connectable(db_host, db_port, db_user, db_password)
    if not ok:
      raise HTTPException(status_code=400, detail='数据库连接失败')
    if not await _check_database_exists(db_host, db_port, db_user, db_password, database_name):
      raise HTTPException(status_code=400, detail=f'数据库不存在：{database_name}')

  use_nginx = bool(payload.use_nginx)
  nginx_data: dict[str, str] = {}
  if use_nginx:
    nginx_data = await _validate_sync_nginx_config(
      servers=servers,
      project_server_row=server_row,
      project_name=project_name,
      server_ip=server_ip,
      nginx_server_ip=payload.nginx_server_ip,
      requested_conf_path=payload.nginx_conf_path,
      frontend_port=payload.frontend_port,
      backend_deploy_port=payload.backend_deploy_port,
      nginx_config_text=payload.nginx_config_text,
    )

  frontend_path = frontend_root_for_project(current_user, is_root, project_name) if use_nginx else None
  created = await crud.projects.create(
    session,
    obj_in={
      'owner_id': current_user.id,
      'server_id': server_row.id,
      'name': project_name,
      'description': (payload.description or '').strip() or None,
      'backend_path': backend_path,
      'frontend_path': frontend_path,
      'nginx_conf_path': nginx_data.get('nginx_conf_path') if use_nginx else None,
      'nginx_server_ip': nginx_data.get('nginx_server_ip') if use_nginx else None,
      'frontend_port': nginx_data.get('frontend_port') if use_nginx else '',
      'backend_dev_port': '',
      'backend_deploy_port': nginx_data.get('backend_deploy_port') if use_nginx else '',
      'database_name': database_name or None,
      'database_host': db_host if use_database else None,
      'database_port': str(db_port) if use_database and db_port else None,
      'database_user': db_user if use_database else None,
      'database_password': db_password if use_database else None,
      'conda_env_name': conda_name,
      'python_version': python_version,
      'dev_start_command': '',
      'deploy_start_command': '',
      'entry_file_path': entry_file_path,
      'status': 0,
      'nginx_config_text': nginx_data.get('nginx_config_text') if use_nginx else None,
      'created_by': current_user.id,
    },
  )

  actions = [
    f'同步已有项目目录：{backend_path}',
    f'入口文件位置：{entry_file_path or "未配置"}',
    f'同步Conda环境：{conda_name}',
    f'Conda环境路径：{conda_path}',
    f'实际Python版本：{actual_python_version or python_version}',
    f'Nginx配置：{"已配置" if use_nginx else "未配置"}',
    f'数据库配置：{"已配置" if use_database else "未配置"}',
  ]
  await record_project_operation(
    session,
    created,
    current_user,
    action='sync',
    action_label='同步已有项目',
    summary=f'同步已有项目：{project_name}',
    before_data=None,
    after_data=snapshot_project_config(created, {
      'server_ip': str(server_row.ip or ''),
      'conda_env_path': conda_path,
      'conda_python_version': actual_python_version,
    }),
    detail={'actions': actions},
  )

  return schemas.pspm.ProjectSyncResponseData(
    project_id=created.id,
    status='同步成功',
    backend_path=backend_path,
    conda_env_name=conda_name,
    python_version=python_version,
  )
