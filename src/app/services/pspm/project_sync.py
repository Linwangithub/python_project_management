"""项目同步服务模块，负责把服务器上已存在的项目、环境、数据库和 Nginx 配置同步成系统记录。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import os
import shlex
from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_detail import record_project_operation, snapshot_project_config
from app.services.pspm.project_sync_nginx import (
  _validate_sync_nginx_config,
)
from app.services.pspm.project_sync_helpers import (
  _clean_python_version_output,
  _project_base_path_for_user,
  _safe_existing_database_name_from_list,
  _safe_sync_abs_path,
  _safe_sync_backend_path,
  _safe_sync_entry_file_path,
)
from app.services.pspm.project_sync_server_helpers import (
  _get_allowed_server_by_ip,
  _server_directory_exists,
  _server_file_exists,
)
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
  _safe_optional_db_name,
  _safe_db_port,
  _safe_db_user,
)
from app.utils.pspm.path_utils import (
  _safe_conda_name,
  _safe_project_name,
  _safe_rel_path_input,
)
from app.utils.pspm.conda_utils import run_conda_command_on_server, run_shell_in_conda_context_on_server
from app.utils.pspm.shell_utils import (
  _run_server_shell,
  _split_lines,
)



async def _list_directory_children(server_row, path: str) -> list[str]:
  """列出指定服务器目录下的一层子目录名称。

  这个函数同时完成目录存在性检测和目录子项读取，避免同步项目弹框每展开一层目录都执行
  `test -d` 和 `find` 两次服务器命令，从而降低接口超时概率。
  """
  safe_path = shlex.quote(path)
  command = (
    f'if [ ! -d {safe_path} ]; then echo "__PSPM_DIR_NOT_FOUND__"; exit 2; fi; '
    f'find {safe_path} -mindepth 1 -maxdepth 1 -type d ! -name ".*" -printf "%f\\n" | sort'
  )
  code, out, err = await _run_server_shell(server_row, command, timeout=60)
  if code == 2:
    raise HTTPException(status_code=400, detail=f'项目目录不存在：{path}')
  if code != 0:
    raise HTTPException(status_code=500, detail=f'读取项目目录失败：{err.strip() or out.strip() or '未知错误'}')
  return [x.strip() for x in _split_lines(out) if x.strip() and x.strip() != '__PSPM_DIR_NOT_FOUND__']


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
    raise HTTPException(status_code=500, detail=f'读取入口文件目录失败：{err.strip() or out.strip() or '未知错误'}')

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
  """查询指定 Conda 环境的实际路径和 Python 版本。

  该函数复用同一次 Conda 初始化探测结果，并优先直接执行环境目录下的
  `bin/python --version`，避免 `conda run` 在部分 GPU 服务器上响应过慢。
  """
  name = _safe_conda_name(env_name)
  code, out, err = await run_conda_command_on_server(server_row, 'conda info', timeout=120)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or '未知错误'}')

  envs_dir = parse_conda_envs_dir(out)
  if not envs_dir:
    raise HTTPException(status_code=500, detail='未解析到Conda环境目录')

  env_path = f'{envs_dir.rstrip("/")}/{name}'
  safe_env_path = shlex.quote(env_path)
  safe_python_bin = shlex.quote(f'{env_path}/bin/python')
  code_py, out_py, err_py = await run_shell_in_conda_context_on_server(
    server_row,
    (
      f'if [ ! -d {safe_env_path} ]; then exit 3; fi; '
      f'if [ -x {safe_python_bin} ]; then {safe_python_bin} --version; '
      f'else conda run -n {shlex.quote(name)} python --version; fi'
    ),
    timeout=120,
    include_conda_init=True,
  )
  if code_py == 3:
    raise HTTPException(status_code=400, detail=f'Conda环境不存在：{name}')
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
  code, out, err = await run_conda_command_on_server(server_row, 'conda info', timeout=120)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or '未知错误'}')
  envs_dir = parse_conda_envs_dir(out)
  if not envs_dir:
    raise HTTPException(status_code=500, detail='未解析到Conda环境目录')
  envs = await list_conda_env_names_on_server(server_row, envs_dir=envs_dir)
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
    db_host = _safe_db_host(payload.database_host)
    db_port = _safe_db_port(payload.database_port)
    db_user = _safe_db_user(payload.database_user)
    db_password = str(payload.database_password or '')
    ok, _message = await _check_server_mysql_connectable(db_host, db_port, db_user, db_password)
    if not ok:
      raise HTTPException(status_code=400, detail='数据库连接失败')
    visible_databases = await _list_database_names(db_host, db_port, db_user, db_password)
    database_name = _safe_existing_database_name_from_list(payload.database_name, visible_databases)

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
