import asyncio
import re
import shlex
import socket
from typing import List

from fastapi import HTTPException

from app import crud
from app.utils.pspm.project_config import LOCAL_SERVER_IPS


def _list_local_ips() -> set[str]:
  """列出当前后端所在机器可识别的本机 IP。

  参数：
  - 无。

  作用：
  - 判断业务服务器 IP 是否就是当前后端服务器。
  - 如果是本机，则直接执行本地 shell；否则通过 SSH 执行远端 shell。

  返回：
  - IP 集合，包含固定本机地址和 hostname 解析出的地址。
  """
  ips = set(LOCAL_SERVER_IPS)
  try:
    ips.update(socket.gethostbyname_ex(socket.gethostname())[2] or [])
  except Exception:
    pass
  return ips


async def _run_shell(command: str, cwd: str | None = None, timeout: int = 1800) -> tuple[int, str, str]:
  """在当前后端服务器执行 shell 命令。

  参数：
  - command：要执行的 bash 命令。
  - cwd：可选工作目录。
  - timeout：超时时间，单位秒。

  作用：
  - 创建目录、创建 Conda、启动项目、删除项目等底层操作都会调用该函数。

  返回：
  - `(exit_code, stdout, stderr)`。
  """
  proc = await asyncio.create_subprocess_exec(
    '/bin/bash',
    '-lc',
    command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=cwd,
  )
  try:
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
  except asyncio.TimeoutError:
    proc.kill()
    await proc.communicate()
    return 124, '', f'命令执行超时（>{timeout}s）'

  stdout = (stdout_b or b'').decode('utf-8', errors='replace')
  stderr = (stderr_b or b'').decode('utf-8', errors='replace')
  return int(proc.returncode or 0), stdout, stderr


def _split_lines(text: str) -> List[str]:
  """把命令输出文本拆成行列表。

  参数：
  - text：stdout 或 stderr 文本。

  作用：
  - 创建项目接口需要把命令输出流式展示到右侧终端。

  返回：
  - 去掉最后空行后的字符串列表。
  """
  normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
  if not normalized:
    return []
  lines = normalized.split('\n')
  while lines and not lines[-1]:
    lines.pop()
  return lines


def _assert_server_ip_allowed(server_ip: str):
  """校验当前后端是否允许直接操作该服务器 IP。

  参数：
  - server_ip：业务服务器 IP。

  作用：
  - 早期逻辑只允许本机执行；当前保留该保护，用于创建项目前快速发现非法目标。

  返回：
  - 校验通过无返回值。
  """
  if (server_ip or '').strip() in _list_local_ips():
    return
  raise HTTPException(status_code=400, detail=f'当前后端仅支持本机执行，暂不支持服务器 {server_ip}')


def _is_local_server_ip(server_ip: str) -> bool:
  """判断服务器 IP 是否为当前后端本机。

  参数：
  - server_ip：业务服务器 IP。

  返回：
  - True 表示本机；False 表示需要远端 SSH。
  """
  return (server_ip or '').strip() in _list_local_ips()


async def _shell_command_exists(command_name: str) -> bool:
  """检查当前后端服务器是否存在某个命令。

  参数：
  - command_name：命令名称，例如 `sshpass`。

  作用：
  - 远端服务器有 root 密码时，需要 `sshpass` 做非交互 SSH。

  返回：
  - True 表示命令存在。
  """
  safe_name = shlex.quote(str(command_name or '').strip())
  if not safe_name:
    return False
  code, _out, _err = await _run_shell(f'command -v {safe_name} >/dev/null 2>&1', timeout=5)
  return code == 0


async def _run_server_shell(server_row, command: str, timeout: int = 60) -> tuple[int, str, str]:
  """在指定业务服务器执行 shell 命令。

  参数：
  - server_row：服务器 ORM 对象，包含 IP 和 root 密码。
  - command：要执行的命令。
  - timeout：超时时间，单位秒。

  作用：
  - 如果目标服务器就是后端本机，则调用 `_run_shell`。
  - 如果是远端服务器，则通过 SSH 或 sshpass 执行命令。

  返回：
  - `(exit_code, stdout, stderr)`。
  """
  ip = str(getattr(server_row, 'ip', '') or '').strip()
  if not ip:
    return 2, '', '服务器IP不能为空'
  if _is_local_server_ip(ip):
    return await _run_shell(command, timeout=timeout)

  if not re.match(r'^[A-Za-z0-9_.:-]+$', ip):
    return 2, '', f'服务器IP格式不合法：{ip}'

  password = str(getattr(server_row, 'root_password', '') or '')
  ssh_opts = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8'
  remote = f'root@{shlex.quote(ip)}'
  quoted_command = shlex.quote(command)

  if password:
    if not await _shell_command_exists('sshpass'):
      return 127, '', '当前后端未安装sshpass，无法使用root密码进行非交互SSH检测'
    shell_cmd = f'sshpass -p {shlex.quote(password)} ssh {ssh_opts} {remote} {quoted_command}'
  else:
    shell_cmd = f'ssh {ssh_opts} -o BatchMode=yes {remote} {quoted_command}'

  return await _run_shell(shell_cmd, timeout=timeout)


async def _ping_from_server_to_target(source_server_row, target_ip: str) -> tuple[bool, str]:
  """从业务服务器 ping 目标 IP。

  参数：
  - source_server_row：发起 ping 的服务器 ORM 对象。
  - target_ip：目标 IP，通常是 Nginx 服务器 IP。

  作用：
  - 创建项目启用 Nginx 时，需要确认项目服务器能访问 Nginx 服务器。

  返回：
  - `(True, 'ok')` 表示可达。
  - `(False, reason)` 表示不可达。
  """
  target = str(target_ip or '').strip()
  if not target:
    return False, '目标IP不能为空'
  if str(getattr(source_server_row, 'ip', '') or '').strip() == target:
    return True, 'ok'
  if not re.match(r'^[A-Za-z0-9_.:-]+$', target):
    return False, f'目标IP格式不合法：{target}'
  code, out, err = await _run_server_shell(
    source_server_row,
    f'ping -c 1 -W 2 {shlex.quote(target)} >/dev/null 2>&1',
    timeout=15,
  )
  if code == 0:
    return True, 'ok'
  return False, (err.strip() or out.strip() or 'ping不通')


async def _list_allowed_server_rows(session, current_user):
  """查询当前用户可使用的服务器。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。

  作用：
  - 项目创建、Nginx 检测、设置、删除等流程都需要校验服务器权限。

  返回：
  - `crud.servers.get_items` 的分页结果，其中 data 是服务器列表。
  """
  return await crud.servers.get_items(
    session,
    user_id=current_user.id,
    is_root=await crud.rbac.is_root_user(session, user_id=current_user.id),
    page=1,
    page_size=500,
  )


def _find_server_row_by_ip(servers, ip: str):
  """从服务器分页结果中按 IP 查找服务器。

  参数：
  - servers：`_list_allowed_server_rows` 返回值。
  - ip：目标服务器 IP。

  返回：
  - 找到时返回服务器对象；找不到返回 None。
  """
  target = str(ip or '').strip()
  return next((x for x in servers.data if str(x.ip or '').strip() == target), None)


def _find_server_row_by_id(servers, server_id: int | None):
  """从服务器分页结果中按 ID 查找服务器。

  参数：
  - servers：`_list_allowed_server_rows` 返回值。
  - server_id：服务器 ID。

  返回：
  - 找到时返回服务器对象；找不到返回 None。
  """
  if server_id is None:
    return None
  try:
    target = int(server_id)
  except Exception:
    return None
  return next((x for x in servers.data if int(getattr(x, 'id', 0) or 0) == target), None)


def _find_project_nginx_server_row(servers, project):
  """查找项目当前使用的 Nginx 服务器。

  参数：
  - servers：当前用户可使用服务器分页结果。
  - project：项目 ORM 对象。

  作用：
  - 项目设置和删除 Nginx 配置时，需要知道原 Nginx server block 写在哪台服务器。

  返回：
  - 优先按项目表 `nginx_server_ip` 查找；为空时回退到项目所在服务器。
  """
  nginx_ip = str(getattr(project, 'nginx_server_ip', '') or '').strip()
  if nginx_ip:
    return _find_server_row_by_ip(servers, nginx_ip)
  return _find_server_row_by_id(servers, getattr(project, 'server_id', None))
