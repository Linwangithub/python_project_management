import os
import shlex

from fastapi import HTTPException

from app import crud, schemas
from app.services.pspm.project_helpers import (
  get_project_for_user,
  list_conda_env_names_on_server,
  parse_conda_envs_dir,
)
from app.utils.pspm.db_utils import (
  _check_database_exists,
  _check_server_mysql_connectable,
  _safe_db_host,
  _safe_db_port,
  _safe_db_user,
  _safe_optional_db_name,
)
from app.utils.pspm.nginx_utils import (
  _check_nginx_port_conflict,
  _check_nginx_port_conflict_on_server,
  _collect_nginx_conf_inventory_on_server,
  _get_running_nginx_conf_path,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running,
  _is_nginx_running_on_server,
  _is_port_in_use,
  _is_port_in_use_on_server,
)
from app.utils.pspm.path_utils import (
  _build_target_dir,
  _normalize_path,
  _resolve_entry_browser_abs_path,
  _safe_port_number,
  _safe_project_name,
  _safe_rel_path_input,
)
from app.utils.pspm.project_config import CONDA_INIT
from app.utils.pspm.runtime_utils import _inspect_project_runtime
from app.utils.pspm.shell_utils import (
  _assert_server_ip_allowed,
  _find_server_row_by_id,
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _ping_from_server_to_target,
  _run_server_shell,
)


async def list_project_entry_path_children_service(session, current_user, project_id: int, rel_path: str):
  """查询项目入口文件选择器的下一层文件和目录。

  参数：
  - session：数据库会话，由接口层传入。
  - current_user：当前登录用户。
  - project_id：项目 ID，来自 `/entry-path-children` 的 Query 参数。
  - rel_path：相对项目根目录的路径，来自前端入口文件选择器。

  作用：
  - 设置弹框第一步“项目入口文件位置”需要逐层浏览项目目录。
  - 该函数负责校验项目权限、解析安全路径、读取目录子项。

  返回：
  - `ProjectEntryPathNode` 列表，目录节点 `leaf=False`，文件节点 `leaf=True`。

  异常：
  - 项目不存在、无权限、目录不存在、目录无访问权限时抛出 HTTPException。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)

  base_path = _normalize_path(project.backend_path or '')
  abs_dir = _resolve_entry_browser_abs_path(base_path, rel_path)
  if not os.path.isdir(abs_dir):
    raise HTTPException(status_code=400, detail='目录不存在')

  rel_dir = _safe_rel_path_input(rel_path)
  nodes: list[schemas.pspm.ProjectEntryPathNode] = []
  try:
    with os.scandir(abs_dir) as it:
      entries = sorted(list(it), key=lambda x: (not x.is_dir(), x.name.lower()))
      for ent in entries:
        name = ent.name
        if name in {'.', '..'}:
          continue
        child_rel = name if not rel_dir else f'{rel_dir}/{name}'
        if ent.is_dir():
          nodes.append(schemas.pspm.ProjectEntryPathNode(label=f'{name}/', value=child_rel, leaf=False))
        elif ent.is_file():
          nodes.append(schemas.pspm.ProjectEntryPathNode(label=name, value=child_rel, leaf=True))
  except PermissionError:
    raise HTTPException(status_code=403, detail='无权限访问该目录')

  return nodes


async def list_project_conda_envs_service(session, current_user, project_id: int):
  """查询项目所在服务器的 Conda 环境目录和环境名。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID，来自 `/conda-envs` 的 Query 参数。

  作用：
  - 设置弹框的 Conda 环境步骤需要展示当前服务器已有环境。
  - 前端也用该列表判断“选择已有环境”还是“创建新环境”。

  返回：
  - `ProjectCondaEnvListData`，包含 `envs_dir` 和 `envs`。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_id(servers, getattr(project, 'server_id', None))
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该项目服务器使用权限')

  code, out, err = await _run_server_shell(server_row, f'{CONDA_INIT}; conda info', timeout=120)
  if code != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or "unknown error"}')

  envs_dir = parse_conda_envs_dir(out)
  if not envs_dir:
    raise HTTPException(status_code=500, detail='未解析到Conda环境目录')

  envs = await list_conda_env_names_on_server(server_row)
  return schemas.pspm.ProjectCondaEnvListData(envs_dir=envs_dir, envs=envs)


async def check_project_name_service(session, current_user, name: str, base_path: str, server_ip: str):
  """检查项目名称对应的目录是否已经存在。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - name：项目名称，来自前端新建项目弹框。
  - base_path：项目基础路径，例如 `/root/project`。
  - server_ip：业务目标服务器 IP。

  作用：
  - 前端项目名称输入框失去焦点时调用。
  - 后端校验当前用户是否有该服务器权限，并拼出最终项目目录。

  返回：
  - `ProjectNameCheckResponseData`：
    - exists：目录是否存在。
    - target_dir：最终项目目录。
  """
  project_name = _safe_project_name(name)
  normalized_base = _normalize_path(base_path)
  _assert_server_ip_allowed(server_ip)

  servers = await crud.servers.get_items(
    session,
    user_id=current_user.id,
    is_root=await crud.rbac.is_root_user(session, user_id=current_user.id),
    page=1,
    page_size=500,
  )
  if not any(x.ip == server_ip for x in servers.data):
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')

  target_dir = _build_target_dir(normalized_base, project_name)
  return schemas.pspm.ProjectNameCheckResponseData(exists=os.path.exists(target_dir), target_dir=target_dir)


async def check_project_database_service(payload: schemas.pspm.ProjectDatabaseCheckRequest):
  """检查数据库连接，以及目标数据库是否可创建。

  参数：
  - payload.host：数据库 IP。
  - payload.port：数据库端口。
  - payload.username：数据库账号。
  - payload.password：数据库密码。
  - payload.database_name：可选数据库名；创建项目和设置数据库时用于判断是否已存在。

  作用：
  - 新建项目启用数据库时，只在连接成功且目标库不存在时允许继续。
  - 设置项目修改数据库时，同样使用该逻辑判断新数据库名是否可用。

  返回：
  - `ProjectDatabaseCheckResponseData`：
    - ok：连接是否成功。
    - database_exists：目标库是否存在。
    - can_create：目标库是否可创建。
  """
  host = _safe_db_host(payload.host)
  port = _safe_db_port(payload.port)
  username = _safe_db_user(payload.username)
  password = str(payload.password or '')
  database_name = _safe_optional_db_name(str(payload.database_name or ''))

  ok, _message = await _check_server_mysql_connectable(host, port, username, password)
  if not ok:
    raise HTTPException(status_code=400, detail='连接失败')

  database_exists = False
  can_create = True
  message = '连接成功'
  if database_name:
    database_exists = await _check_database_exists(host, port, username, password, database_name)
    can_create = not database_exists
    message = '连接成功，该数据库不存在，可以创建使用' if can_create else '连接成功，但该数据库已经存在，不可创建'

  return schemas.pspm.ProjectDatabaseCheckResponseData(
    ok=True,
    message=message,
    server_mysql_ok=True,
    database_exists=database_exists,
    can_create=can_create,
  )


async def check_project_nginx_service(session, current_user, payload: schemas.pspm.ProjectNginxCheckRequest):
  """检查 Nginx 服务器是否可用，并返回可选配置文件信息。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload.server_ip：项目所在服务器 IP。
  - payload.nginx_server_ip：Nginx 服务器 IP；为空时默认使用 `server_ip`。

  作用：
  - 新建项目启用 Nginx 时，先确认项目服务器能 ping 通 Nginx 服务器。
  - 再确认 Nginx 服务正在运行。
  - 最后扫描正在运行的主配置和顶层/http include 配置文件，返回给前端选择。

  返回：
  - `ProjectNginxCheckResponseData`，包含主配置路径、可选配置文件、可新建配置目录。
  """
  server_ip = (payload.server_ip or '').strip()
  nginx_server_ip = (payload.nginx_server_ip or server_ip).strip()
  if not server_ip:
    raise HTTPException(status_code=400, detail='服务器IP不能为空')
  if not nginx_server_ip:
    raise HTTPException(status_code=400, detail='Nginx服务器IP不能为空')

  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_ip(servers, server_ip)
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')
  nginx_server_row = _find_server_row_by_ip(servers, nginx_server_ip)
  if not nginx_server_row:
    raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

  ping_ok, ping_msg = await _ping_from_server_to_target(server_row, nginx_server_ip)
  if not ping_ok:
    raise HTTPException(status_code=400, detail=f'Nginx服务器不可达：{ping_msg}')

  running = await _is_nginx_running_on_server(nginx_server_row)
  if not running:
    raise HTTPException(status_code=400, detail='nginx服务未开启')

  conf_path = await _get_running_nginx_conf_path_on_server(nginx_server_row)
  inventory = await _collect_nginx_conf_inventory_on_server(nginx_server_row, conf_path)
  return schemas.pspm.ProjectNginxCheckResponseData(
    ok=True,
    running=True,
    conf_path=conf_path,
    conf_files=[schemas.pspm.ProjectNginxConfigFile(**item) for item in inventory.get('conf_files', [])],
    new_conf_dirs=[schemas.pspm.ProjectNginxNewConfDir(**item) for item in inventory.get('new_conf_dirs', [])],
    message='nginx服务可用',
  )


async def check_project_port_service(session, current_user, payload: schemas.pspm.ProjectPortCheckRequest):
  """检查端口是否可用。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload.port：要检测的端口。
  - payload.project_id：可选项目 ID；大于 0 时会校验项目权限，并用于识别当前项目。
  - payload.nginx_server_ip：可选 Nginx 服务器 IP。
  - payload.check_nginx_conf：是否检查 Nginx 配置中的 listen/proxy_pass 端口。

  作用：
  - 创建项目和设置项目中的 Nginx 前端端口、后端部署端口都调用该逻辑。
  - 同时检查系统监听端口和 Nginx 配置端口冲突。

  返回：
  - `ProjectPortCheckResponseData`，成功时表示端口可用。

  异常：
  - 端口不合法、端口被系统占用、端口被 Nginx listen/proxy_pass 占用时抛出 HTTP 400。
  """
  project = None
  project_id = int(payload.project_id or 0)
  if project_id > 0:
    project, _is_root = await get_project_for_user(session, project_id, current_user)

  port = _safe_port_number(int(payload.port))
  server_row = None
  if payload.check_nginx_conf and str(getattr(payload, 'nginx_server_ip', '') or '').strip():
    servers = await _list_allowed_server_rows(session, current_user)
    server_row = _find_server_row_by_ip(servers, str(payload.nginx_server_ip or '').strip())
    if not server_row:
      raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

  in_use = await _is_port_in_use_on_server(server_row, port) if server_row else await _is_port_in_use(port)
  nginx_conflict = False
  nginx_listen_conflict = False
  nginx_proxy_conflict = False
  conf_path = ''
  project_name_for_conflict = str(getattr(project, 'name', '') or '').strip() if project is not None else ''

  if payload.check_nginx_conf:
    if server_row:
      running = await _is_nginx_running_on_server(server_row)
      if not running:
        raise HTTPException(status_code=400, detail='nginx服务未开启')
      conf_path = await _get_running_nginx_conf_path_on_server(server_row)
      conflict = await _check_nginx_port_conflict_on_server(server_row, port, conf_path, project_name=project_name_for_conflict)
    else:
      running = await _is_nginx_running()
      if not running:
        raise HTTPException(status_code=400, detail='nginx服务未开启')
      conf_path = await _get_running_nginx_conf_path()
      conflict = await _check_nginx_port_conflict(port, conf_path, project_name=project_name_for_conflict)
    nginx_listen_conflict = bool(conflict.get('listen'))
    nginx_proxy_conflict = bool(conflict.get('proxy_pass'))
    nginx_conflict = nginx_listen_conflict or nginx_proxy_conflict

  if in_use or nginx_conflict:
    msg_parts = []
    if in_use:
      msg_parts.append(f'端口 {port} 已被系统占用')
    if nginx_listen_conflict:
      msg_parts.append(f'端口 {port} 已在Nginx listen 配置中占用')
    if nginx_proxy_conflict:
      msg_parts.append(f'端口 {port} 已在Nginx proxy_pass 配置中占用')
    raise HTTPException(status_code=400, detail='；'.join(msg_parts))

  return schemas.pspm.ProjectPortCheckResponseData(
    ok=True,
    port=port,
    range_ok=True,
    in_use=False,
    nginx_conflict=False,
    nginx_conf_path=conf_path,
    message='端口可用',
  )



def _join_summary_parts(parts: list[str], empty_text: str = '未配置') -> str:
  """拼接项目列表复合字段的已配置部分。"""
  values = [str(item or '').strip() for item in parts if str(item or '').strip()]
  return ' / '.join(values) if values else empty_text


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
      problems.append('项目服务器不可用或无权限')
      item.service_status = '已停止'
      item.running_port = ''
    else:
      runtime_data = await _inspect_project_runtime(server_row, item)
      item.service_status = runtime_data.get('service_status') or '已停止'
      item.running_port = runtime_data.get('running_port') or ''

      if item.backend_path and not await _server_path_exists(server_row, item.backend_path):
        problems.append('项目目录不存在')
      if item.conda_env_name and not await _server_conda_env_exists(server_row, item.conda_env_name):
        problems.append('Conda环境不存在')

    if item.database_name:
      try:
        host = _safe_db_host(item.database_host or '')
        port = _safe_db_port(int(item.database_port or 3306))
        user = _safe_db_user(item.database_user or '')
        password = str(item.database_password or '')
        ok, _message = await _check_server_mysql_connectable(host, port, user, password)
        if not ok:
          problems.append('数据库连接失败')
        elif not await _check_database_exists(host, port, user, password, item.database_name):
          problems.append('数据库不存在')
      except Exception:
        problems.append('数据库检测失败')

    if item.nginx_conf_path or item.nginx_server_ip or item.frontend_port or item.backend_deploy_port:
      nginx_row = server_by_ip.get(str(item.nginx_server_ip or '').strip()) or server_row
      if not nginx_row:
        problems.append('Nginx服务器不可用或无权限')
      elif not await _is_nginx_running_on_server(nginx_row):
        problems.append('Nginx服务未运行')
      elif item.nginx_conf_path:
        ok_conf = await _nginx_conf_contains_project_config(
          nginx_row,
          item.nginx_conf_path,
          item.frontend_port or '',
          item.backend_deploy_port or '',
        )
        if not ok_conf:
          problems.append('Nginx配置不匹配或文件不存在')

    item.nginx_info = _join_summary_parts([
      item.nginx_server_ip or '',
      f'前端:{item.frontend_port}' if item.frontend_port else '',
      f'后端:{item.backend_deploy_port}' if item.backend_deploy_port else '',
    ])
    item.database_info = _join_summary_parts([
      item.database_host or '',
      f'库:{item.database_name}' if item.database_name else '',
    ])
    item.project_status = '异常' if problems else '正常'
    item.project_status_detail = '；'.join(problems)
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
    status=crud.projects.model and crud.project_status_to_name(project.status) if False else ('运行中' if project.status == 1 else '已停止'),
    created_at=project.created_at,
  )
  item.nginx_info = _join_summary_parts([
    item.nginx_server_ip or '',
    f'前端:{item.frontend_port}' if item.frontend_port else '',
    f'后端:{item.backend_deploy_port}' if item.backend_deploy_port else '',
  ])
  item.database_info = _join_summary_parts([
    item.database_host or '',
    f'库:{item.database_name}' if item.database_name else '',
  ])
  item.project_status = '未检测'
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
    problems.append('项目服务器不可用或无权限')
    item.service_status = '已停止'
    item.running_port = ''
  else:
    runtime_data = await _inspect_project_runtime(server_row, item)
    item.service_status = runtime_data.get('service_status') or '已停止'
    item.running_port = runtime_data.get('running_port') or ''
    if item.backend_path and not await _server_path_exists(server_row, item.backend_path):
      problems.append('项目目录不存在')
    if item.conda_env_name and not await _server_conda_env_exists(server_row, item.conda_env_name):
      problems.append('Conda环境不存在')

  if item.database_name:
    try:
      host = _safe_db_host(item.database_host or '')
      port = _safe_db_port(int(item.database_port or 3306))
      user = _safe_db_user(item.database_user or '')
      password = str(item.database_password or '')
      ok, _message = await _check_server_mysql_connectable(host, port, user, password)
      if not ok:
        problems.append('数据库连接失败')
      elif not await _check_database_exists(host, port, user, password, item.database_name):
        problems.append('数据库不存在')
    except Exception:
      problems.append('数据库检测失败')

  if item.nginx_conf_path or item.nginx_server_ip or item.frontend_port or item.backend_deploy_port:
    nginx_row = server_by_ip.get(str(item.nginx_server_ip or '').strip()) or server_row
    if not nginx_row:
      problems.append('Nginx服务器不可用或无权限')
    elif not await _is_nginx_running_on_server(nginx_row):
      problems.append('Nginx服务未运行')
    elif item.nginx_conf_path:
      ok_conf = await _nginx_conf_contains_project_config(
        nginx_row,
        item.nginx_conf_path,
        item.frontend_port or '',
        item.backend_deploy_port or '',
      )
      if not ok_conf:
        problems.append('Nginx配置不匹配或文件不存在')

  item.project_status = '异常' if problems else '正常'
  item.project_status_detail = '；'.join(problems)
  item.status = item.service_status
  return item

