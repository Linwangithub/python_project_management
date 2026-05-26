"""项目检测服务模块，负责服务器、端口、Conda、数据库和 Nginx 的校验流程。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

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
from app.utils.pspm.shell_utils import (
  _find_server_row_by_id,
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _ping_from_server_to_target,
  _run_server_shell,
  _same_ip,
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
    raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or '未知错误'}')

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
  - base_path：项目基础路径，例如“项目基础路径配置值”。
  - server_ip：业务目标服务器 IP。

  作用：
  - 前端项目名称输入框失去焦点时调用。
  - 后端校验当前用户是否有该服务器权限，并在目标服务器上真实检测目录是否存在。
  - 分布式场景下不能使用后端本机的 `os.path.exists`，否则会把远程路径误判成本机路径。

  返回：
  - `ProjectNameCheckResponseData`：
    - exists：目标服务器上目录是否存在。
    - target_dir：最终项目目录。
  """
  project_name = _safe_project_name(name)
  normalized_base = _normalize_path(base_path)
  target_ip = str(server_ip or '').strip()
  if not target_ip:
    raise HTTPException(status_code=400, detail='服务器IP不能为空')

  # 先按当前用户权限读取可操作服务器，再从授权列表中匹配页面选择的 IP。
  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_ip(servers, target_ip)
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')

  target_dir = _build_target_dir(normalized_base, project_name)
  # 使用远程 shell 检测目录/文件是否存在；目标是远端时走 SSH，目标是本机时自动走本地 shell。
  code, out, err = await _run_server_shell(server_row, f'test -e {shlex.quote(target_dir)}', timeout=15)
  if code in (0, 1):
    return schemas.pspm.ProjectNameCheckResponseData(exists=(code == 0), target_dir=target_dir)

  message = err.strip() or out.strip() or '未知错误'
  raise HTTPException(status_code=500, detail=f'检测项目目录失败：{message}')


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
  requested_nginx_ip = str(getattr(payload, 'nginx_server_ip', '') or '').strip()
  if payload.check_nginx_conf and requested_nginx_ip:
    servers = await _list_allowed_server_rows(session, current_user)
    server_row = _find_server_row_by_ip(servers, requested_nginx_ip)
    if not server_row:
      raise HTTPException(status_code=403, detail='当前用户无该Nginx服务器使用权限')

  project_name_for_conflict = str(getattr(project, 'name', '') or '').strip() if project is not None else ''
  original_nginx_ip = str(getattr(project, 'nginx_server_ip', '') or '').strip() if project is not None else ''
  original_ports = {
    str(getattr(project, 'frontend_port', '') or '').strip(),
    str(getattr(project, 'backend_deploy_port', '') or '').strip(),
  }
  is_current_project_port = bool(
    project is not None
    and requested_nginx_ip
    and original_nginx_ip
    and _same_ip(requested_nginx_ip, original_nginx_ip)
    and str(port) in original_ports
  )
  ignore_block_text = str(getattr(project, 'nginx_config_text', '') or '') if is_current_project_port else ''

  in_use = await _is_port_in_use_on_server(server_row, port) if server_row else await _is_port_in_use(port)
  # 当前项目已绑定的端口可能正被当前项目自己的 Nginx 或后端进程监听。
  # 设置弹框回显原配置或修改其他字段时，这种“自己占用自己”的情况不应阻塞下一步。
  if is_current_project_port:
    in_use = False

  nginx_conflict = False
  nginx_listen_conflict = False
  nginx_proxy_conflict = False
  conf_path = ''

  if payload.check_nginx_conf:
    if server_row:
      running = await _is_nginx_running_on_server(server_row)
      if not running:
        raise HTTPException(status_code=400, detail='nginx服务未开启')
      conf_path = await _get_running_nginx_conf_path_on_server(server_row)
      conflict = await _check_nginx_port_conflict_on_server(
        server_row,
        port,
        conf_path,
        project_name=project_name_for_conflict,
        ignore_block_text=ignore_block_text,
      )
    else:
      running = await _is_nginx_running()
      if not running:
        raise HTTPException(status_code=400, detail='nginx服务未开启')
      conf_path = await _get_running_nginx_conf_path()
      conflict = await _check_nginx_port_conflict(
        port,
        conf_path,
        project_name=project_name_for_conflict,
        ignore_block_text=ignore_block_text,
      )
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

