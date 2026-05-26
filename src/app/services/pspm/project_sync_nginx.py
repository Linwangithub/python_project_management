"""同步已有项目的 Nginx 配置校验服务。

本模块集中维护同步项目绑定已有 Nginx server 块的逻辑，包括读取配置文件、
提取 listen/proxy_pass 端口选项、校验选择的 server 块以及生成配置快照。
"""

from __future__ import annotations

import re

from fastapi import HTTPException

from app import schemas
from app.services.pspm.project_sync_server_helpers import (
  _get_allowed_server_by_ip,
  _server_file_exists,
)
from app.utils.pspm.nginx_utils import (
  _collect_nginx_conf_inventory_on_server,
  _get_running_nginx_conf_path_on_server,
  _is_nginx_running_on_server,
  _read_text_on_server,
  _validate_requested_nginx_conf_path,
)
from app.utils.pspm.nginx_server_blocks import (
  _find_server_block_ranges,
  _server_block_listen_ports,
  _server_block_proxy_pass_ports,
)
from app.utils.pspm.path_utils import _safe_optional_port_text
from app.utils.pspm.shell_utils import _find_server_row_by_ip, _ping_from_server_to_target


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

  # 同步已有项目绑定的是已经存在的 server 块，listen 端口本来就应该已被 Nginx 使用，
  # 因此这里不能沿用创建项目的“端口未占用”校验，只校验配置文件里是否存在匹配 server 块。
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

def _extract_nginx_server_name(block_text: str) -> str:
  """从单个 Nginx server 块中提取 server_name。

  参数：
  - block_text：一个完整的 server 块文本。

  返回：
  - 第一个 server_name 值；没有配置时返回空字符串。
  """
  import re

  match = re.search(r'(?m)^\s*server_name\s+([^;]+);', str(block_text or ''))
  if not match:
    return ''
  return ' '.join(match.group(1).strip().split())

def _list_nginx_server_port_options(conf_text: str) -> list[schemas.pspm.ProjectSyncNginxServerPortOption]:
  """从 Nginx 配置文件文本中列出可同步的 server 端口组合。

  参数：
  - conf_text：用户选择的 Nginx 配置文件完整文本。

  作用：
  - 同步已有项目时，前端端口必须从已有 listen 端口里选择。
  - 后端部署端口由同一个 server 块中的 proxy_pass 端口自动回显。

  返回：
  - 每个包含 listen 和 proxy_pass 的 server 块组合。
  """
  result: list[schemas.pspm.ProjectSyncNginxServerPortOption] = []
  seen: set[tuple[int, int, str]] = set()
  text = str(conf_text or '')
  for start, end in _find_server_block_ranges(text):
    block = text[start:end].strip()
    listen_ports = sorted(_server_block_listen_ports(block))
    proxy_ports = sorted(_server_block_proxy_pass_ports(block))
    if not listen_ports or not proxy_ports:
      continue
    server_name = _extract_nginx_server_name(block)
    block_text = block if block.endswith('\n') else f'{block}\n'
    for listen_port in listen_ports:
      for proxy_port in proxy_ports:
        key = (listen_port, proxy_port, block_text)
        if key in seen:
          continue
        seen.add(key)
        label_extra = f' · {server_name}' if server_name else ''
        result.append(schemas.pspm.ProjectSyncNginxServerPortOption(
          label=f'{listen_port} → {proxy_port}{label_extra}',
          frontend_port=str(listen_port),
          backend_deploy_port=str(proxy_port),
          server_name=server_name,
          nginx_config_text=block_text,
        ))
  return result

async def list_sync_nginx_server_port_options_service(session, current_user, payload: schemas.pspm.ProjectSyncNginxServerPortOptionsRequest):
  """查询同步已有项目可选择的 Nginx 前端端口和后端代理端口。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - payload：项目服务器 IP、Nginx 服务器 IP、已选择的 Nginx 配置文件路径。

  作用：
  - 读取用户选择的真实 Nginx 配置文件。
  - 提取所有包含 listen 和 proxy_pass 的 server 块。
  - 前端据此把 Nginx 前端端口渲染为下拉框，后端部署端口自动回显。

  返回：
  - `ProjectSyncNginxServerPortOptionsData`，其中 options 是可同步的端口组合列表。
  """
  servers, project_server_row = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)
  server_ip = str(payload.server_ip or '').strip()
  nginx_ip = str(payload.nginx_server_ip or server_ip).strip()
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
  nginx_conf_path = _validate_requested_nginx_conf_path(payload.nginx_conf_path, inventory)
  if not await _server_file_exists(nginx_server_row, nginx_conf_path):
    raise HTTPException(status_code=400, detail=f'Nginx配置文件不存在：{nginx_conf_path}')

  ok, conf_text = await _read_text_on_server(nginx_server_row, nginx_conf_path)
  if not ok:
    raise HTTPException(status_code=400, detail=f'读取Nginx配置失败：{conf_text}')

  options = _list_nginx_server_port_options(conf_text)
  if not options:
    raise HTTPException(status_code=400, detail='所选Nginx配置文件中没有可同步的 listen + proxy_pass server 块')
  return schemas.pspm.ProjectSyncNginxServerPortOptionsData(options=options)

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
