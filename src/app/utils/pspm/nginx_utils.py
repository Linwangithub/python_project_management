"""Nginx 工具模块，封装配置文件发现、server 块解析和配置写入检测逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import json
import os
import re

from fastapi import HTTPException

from app.utils.pspm.nginx_apply import (
  _apply_nginx_conf_change,
  _apply_nginx_conf_change_on_server,
  _read_text_on_server,
)
from app.utils.pspm.nginx_server_blocks import (
  _find_server_block_ranges,
  _server_block_contains_project,
  _server_block_listen_ports,
  _server_block_proxy_pass_ports,
)
from app.utils.pspm.nginx_remote_inventory import _build_remote_nginx_inventory_command
from app.utils.pspm.nginx_inventory import (
  _collect_nginx_conf_files,
  _collect_nginx_conf_inventory,
)
from app.utils.pspm.shell_utils import _is_local_server_ip, _run_server_shell, _run_shell, _split_lines


async def _is_port_in_use(port: int) -> bool:
  """检查当前后端服务器的端口是否被系统监听占用。

  参数：
  - port：要检测的端口。

  作用：
  - 创建项目和设置项目时，检测 Nginx 前端端口或后端部署端口是否已被系统进程占用。

  返回：
  - True 表示端口已被占用；False 表示端口未监听。
  """
  check_cmd = (
    "ss -lntH | awk '{print $4}' "
    "| sed -E 's#.*:([0-9]+)$#\\1#' "
    f"| grep -x {int(port)} >/dev/null 2>&1"
  )
  code, _out, _err = await _run_shell(check_cmd, timeout=10)
  return code == 0


async def _is_nginx_running() -> bool:
  """检查当前后端服务器上的 Nginx 是否运行。"""
  code, _out, _err = await _run_shell('pgrep -x nginx >/dev/null 2>&1', timeout=10)
  return code == 0


def _parse_nginx_conf_path(text: str) -> str:
  """从 `nginx -V` 输出中解析编译时默认配置文件路径。"""
  m = re.search(r'--conf-path=([^\s]+)', text or '')
  if m:
    return m.group(1).strip()
  return ''


async def _get_running_nginx_conf_path() -> str:
  """获取当前后端服务器正在运行的 Nginx 主配置文件路径。

  作用：
  - 优先从 `pgrep -a -x nginx` 的 `-c` 参数读取运行中配置文件。
  - 如果进程参数没有指定 `-c`，再从 `nginx -V` 的 `--conf-path` 读取默认路径。
  """
  code, out, _err = await _run_shell("pgrep -a -x nginx | head -n 1", timeout=10)
  if code == 0 and out.strip():
    line = out.strip()
    m = re.search(r'\s-c\s+([^\s]+)', line)
    if m:
      return m.group(1).strip()

  _code2, out2, err2 = await _run_shell('nginx -V 2>&1', timeout=10)
  merged = '\n'.join(_split_lines(out2) + _split_lines(err2))
  conf = _parse_nginx_conf_path(merged)
  if conf:
    return conf
  raise HTTPException(status_code=400, detail='未解析到nginx配置路径')


async def _is_nginx_running_on_server(server_row) -> bool:
  """检查指定业务服务器上的 Nginx 是否运行。

  参数：
  - server_row：服务器 ORM 对象。

  返回：
  - True 表示运行中；False 表示未运行。
  """
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _is_nginx_running()
  code, _out, _err = await _run_server_shell(server_row, 'pgrep -x nginx >/dev/null 2>&1', timeout=15)
  return code == 0


async def _get_running_nginx_conf_path_on_server(server_row) -> str:
  """获取指定业务服务器正在运行的 Nginx 主配置文件路径。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _get_running_nginx_conf_path()

  code, out, _err = await _run_server_shell(server_row, "pgrep -a -x nginx | head -n 1", timeout=15)
  if code == 0 and out.strip():
    line = out.strip().splitlines()[0]
    m = re.search(r'\s-c\s+([^\s]+)', line)
    if m:
      return m.group(1).strip()

  _code2, out2, err2 = await _run_server_shell(server_row, 'nginx -V 2>&1', timeout=20)
  merged = '\n'.join(_split_lines(out2) + _split_lines(err2))
  conf = _parse_nginx_conf_path(merged)
  if conf:
    return conf
  raise HTTPException(status_code=400, detail='未解析到nginx配置路径')


async def _collect_nginx_conf_inventory_on_server(server_row, main_conf_path: str) -> dict[str, list[dict[str, str]]]:
  """收集指定业务服务器上的 Nginx 配置清单。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return _collect_nginx_conf_inventory(main_conf_path)

  command = _build_remote_nginx_inventory_command(main_conf_path)
  code, out, err = await _run_server_shell(server_row, command, timeout=30)
  if code != 0:
    msg = err.strip() or out.strip() or '读取Nginx配置失败'
    raise HTTPException(status_code=400, detail=msg)

  try:
    line = out.strip().splitlines()[-1] if out.strip() else '{}'
    data = json.loads(line)
  except Exception as ex:
    raise HTTPException(status_code=500, detail=f'解析Nginx配置结果失败：{str(ex)}')

  if not isinstance(data, dict):
    return {'conf_files': [], 'new_conf_dirs': []}
  conf_files = data.get('conf_files') if isinstance(data.get('conf_files'), list) else []
  new_conf_dirs = data.get('new_conf_dirs') if isinstance(data.get('new_conf_dirs'), list) else []
  return {'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}


def _validate_requested_nginx_conf_path(requested_path: str, inventory: dict[str, list[dict[str, str]]]) -> str:
  """校验前端选择或新建的 Nginx 配置文件路径是否合法。"""
  conf_path = os.path.normpath(str(requested_path or '').strip())
  if not conf_path:
    raise HTTPException(status_code=400, detail='请选择Nginx配置文件路径')
  if not conf_path.startswith('/'):
    raise HTTPException(status_code=400, detail='Nginx配置文件路径必须是绝对路径')

  existing_paths = {
    os.path.normpath(str(item.get('path') or '').strip())
    for item in inventory.get('conf_files', [])
    if str(item.get('path') or '').strip()
  }
  if conf_path in existing_paths:
    return conf_path

  filename = os.path.basename(conf_path)
  if not filename or '/' in filename or '\\' in filename or not filename.lower().endswith('.conf'):
    raise HTTPException(status_code=400, detail='新建Nginx配置文件名必须以 .conf 结尾，且不能包含路径分隔符')

  parent_dir = os.path.dirname(conf_path)
  allowed_dirs = {
    os.path.normpath(str(item.get('directory') or '').strip())
    for item in inventory.get('new_conf_dirs', [])
    if str(item.get('directory') or '').strip()
  }
  if parent_dir not in allowed_dirs:
    raise HTTPException(status_code=400, detail='新建Nginx配置文件必须位于主配置顶层include或http块include允许的目录中')
  return conf_path


def _read_nginx_conf_text(conf_path: str) -> str:
  """读取本机 Nginx 配置文件文本。"""
  if not conf_path or not os.path.isfile(conf_path):
    return ''
  try:
    with open(conf_path, 'r', encoding='utf-8', errors='replace') as f:
      return f.read()
  except Exception:
    return ''


def _normalize_nginx_block_for_compare(block_text: str) -> str:
  """把 Nginx server 块标准化为可比较文本。

  参数：
  - block_text：一个 server 块文本，可能来自真实配置文件或数据库配置快照。

  作用：
  - 同步已有项目时，真实 Nginx server 块可能没有 `# pspm_project` 标记。
  - 设置或端口检测时，需要识别“当前项目自己的旧配置块”，避免把自己的端口误判为冲突。
  - 比较时忽略空行、缩进和项目标记行，但保留普通配置行，避免过度放宽匹配。

  返回：
  - 标准化后的多行文本；空输入返回空字符串。
  """
  normalized_lines: list[str] = []
  for raw_line in str(block_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
    line = raw_line.strip()
    if not line:
      continue
    if re.match(r'^#\s*pspm_project\s+[^\s]+\s*$', line):
      continue
    if re.match(r'^pspm_project\s+[^\s]+\s*;\s*$', line):
      continue
    normalized_lines.append(re.sub(r'\s+', ' ', line))
  return '\n'.join(normalized_lines)


def _should_ignore_nginx_conflict_block(block_text: str, project_name: str = '', ignore_block_text: str = '') -> bool:
  """判断端口冲突检测时是否应忽略某个 server 块。

  参数：
  - block_text：当前正在扫描的真实 Nginx server 块。
  - project_name：当前项目名称；用于识别带 `# pspm_project` 标记的系统生成块。
  - ignore_block_text：数据库保存的当前项目原始 Nginx 配置快照。

  作用：
  - 新建项目时不传 `ignore_block_text`，仍然严格检测所有已有端口。
  - 设置/同步已有项目时传入当前项目旧配置，避免当前项目自己的 listen/proxy_pass 被当成别人的冲突。

  返回：
  - True 表示这是当前项目自己的块，可以跳过；False 表示需要参与冲突检测。
  """
  if project_name and _server_block_contains_project(block_text, project_name):
    return True
  current_block = _normalize_nginx_block_for_compare(block_text)
  ignored_block = _normalize_nginx_block_for_compare(ignore_block_text)
  return bool(current_block and ignored_block and current_block == ignored_block)


async def _check_nginx_port_conflict(
  port: int,
  conf_path: str,
  project_name: str = '',
  ignore_block_text: str = '',
) -> dict[str, bool | str]:
  """检查本机 Nginx 配置中端口是否冲突。

  参数：
  - port：要检测的端口。
  - conf_path：Nginx 主配置文件路径。
  - project_name：当前项目名称；用于忽略带项目标记的当前项目块。
  - ignore_block_text：当前项目数据库中保存的旧 server 块快照。

  返回：
  - dict：listen/proxy_pass 是否冲突，以及命中的配置文件路径。
  """
  result: dict[str, bool | str] = {'listen': False, 'proxy_pass': False, 'conf_path': ''}
  candidates = _collect_nginx_conf_files(conf_path)
  if not candidates and conf_path and os.path.isfile(conf_path):
    candidates = [{'path': os.path.normpath(conf_path), 'source': 'main'}]

  for item in candidates:
    path = str(item.get('path') or '').strip()
    if not path or not os.path.isfile(path):
      continue
    conf_text = _read_nginx_conf_text(path)
    if not conf_text:
      continue
    for block_start, block_end in _find_server_block_ranges(conf_text):
      block = conf_text[block_start:block_end]
      if _should_ignore_nginx_conflict_block(block, project_name=project_name, ignore_block_text=ignore_block_text):
        continue
      if port in _server_block_listen_ports(block):
        result['listen'] = True
        result['conf_path'] = path
      if port in _server_block_proxy_pass_ports(block):
        result['proxy_pass'] = True
        result['conf_path'] = path
      if result['listen'] or result['proxy_pass']:
        return result
  return result


async def _check_nginx_listen_conflict(
  port: int,
  conf_path: str,
  project_name: str = '',
  ignore_block_text: str = '',
) -> bool:
  """兼容旧调用：只检查 listen 端口是否冲突。"""
  conflict = await _check_nginx_port_conflict(
    port,
    conf_path,
    project_name=project_name,
    ignore_block_text=ignore_block_text,
  )
  return bool(conflict.get('listen'))


async def _check_nginx_port_conflict_on_server(
  server_row,
  port: int,
  conf_path: str,
  project_name: str = '',
  ignore_block_text: str = '',
) -> dict[str, bool | str]:
  """检查指定业务服务器 Nginx 配置中的端口冲突。

  参数：
  - server_row：Nginx 所在服务器记录。
  - port：要检测的端口。
  - conf_path：Nginx 主配置文件路径。
  - project_name：当前项目名称；用于忽略系统生成的当前项目块。
  - ignore_block_text：当前项目原 Nginx server 块快照。

  返回：
  - dict：listen/proxy_pass 是否冲突，以及命中的配置文件路径。
  """
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _check_nginx_port_conflict(
      port,
      conf_path,
      project_name=project_name,
      ignore_block_text=ignore_block_text,
    )

  result: dict[str, bool | str] = {'listen': False, 'proxy_pass': False, 'conf_path': ''}
  inventory = await _collect_nginx_conf_inventory_on_server(server_row, conf_path)
  candidates = inventory.get('conf_files', []) or []
  if not candidates and conf_path:
    candidates = [{'path': os.path.normpath(conf_path), 'source': 'main'}]

  for item in candidates:
    path = str(item.get('path') or '').strip()
    if not path:
      continue
    ok, conf_text = await _read_text_on_server(server_row, path)
    if not ok or not conf_text:
      continue
    for block_start, block_end in _find_server_block_ranges(conf_text):
      block = conf_text[block_start:block_end]
      if _should_ignore_nginx_conflict_block(block, project_name=project_name, ignore_block_text=ignore_block_text):
        continue
      if port in _server_block_listen_ports(block):
        result['listen'] = True
        result['conf_path'] = path
      if port in _server_block_proxy_pass_ports(block):
        result['proxy_pass'] = True
        result['conf_path'] = path
      if result['listen'] or result['proxy_pass']:
        return result
  return result


async def _is_port_in_use_on_server(server_row, port: int) -> bool:
  """检查指定业务服务器系统端口是否被监听占用。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _is_port_in_use(port)
  check_cmd = (
    "ss -lntH | awk '{print $4}' "
    "| sed -E 's#.*:([0-9]+)$#\\1#' "
    f"| grep -x {int(port)} >/dev/null 2>&1"
  )
  code, _out, _err = await _run_server_shell(server_row, check_cmd, timeout=15)
  return code == 0


