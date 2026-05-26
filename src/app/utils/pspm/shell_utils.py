"""Shell 工具模块，封装本机和远程服务器命令执行、SSH 连接和输出处理。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import asyncio
import ipaddress
import re
import shlex
import socket
from typing import List

from fastapi import HTTPException

from app import crud
from app.utils.pspm.project_config import LOCAL_SERVER_IPS, SSH_ASKPASS_TEMPLATE
from app.utils.pspm.shell_config import (
  PING_OK_MESSAGE,
  SAFE_HOST_RE,
  SSH_BATCH_MODE_OPTION,
  SSH_DEFAULT_OPTIONS,
  render_shell_error,
)


def _list_local_ips() -> set[str]:
  """列出当前后端所在机器可识别的本机 IP。

  参数：
  - 无。

  作用：
  - 判断业务服务器 IP 是否就是当前后端服务器。
  - 如果是本机，则直接执行本地 shell；否则通过 SSH 执行远端 shell。

  返回：
  - IP 集合，包含回环地址、配置兜底地址和 hostname 解析出的地址。
  """
  # LOCAL_SERVER_IPS 是配置兜底，后面再叠加系统实际网卡地址。
  ips = {str(x or '').strip() for x in LOCAL_SERVER_IPS if str(x or '').strip()}
  # 回环地址和 localhost 永远视为本机，避免本机操作绕远端 SSH。
  ips.update({'127.0.0.1', '::1', 'localhost'})
  try:
    ips.update(socket.gethostbyname_ex(socket.gethostname())[2] or [])
  except Exception:
    pass
  try:
    for item in socket.getaddrinfo(socket.gethostname(), None):
      addr = item[4][0]
      if addr:
        ips.add(addr)
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


async def _list_local_ips_async() -> set[str]:
  """动态读取当前后端机器真实网卡 IP。

  参数：
  - 无。

  作用：
  - 服务器管理中的 IP 存在数据库里，不应该写死在代码中。
  - 当数据库里的服务器 IP 正好是当前后端机器自身 IP 时，直接本机执行命令，不走 SSH。
  - 当数据库里的服务器 IP 是其他机器时，才走 SSH/sshpass。

  返回：
  - 当前后端机器可识别的本机 IP 集合。
  """
  ips = _list_local_ips()
  commands = [
    "hostname -I 2>/dev/null || true",
    "ip -o -4 addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true",
    "ip -o -6 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true",
  ]
  for command in commands:
    # 同时兼容 hostname -I 和 ip addr 两类输出，提升不同 Linux 发行版下的本机识别准确性。
    code, out, _err = await _run_shell(command, timeout=5)
    if code != 0:
      continue
    for token in re.split(r'\s+', out.strip()):
      value = token.strip()
      if value:
        ips.add(value)
  return {x for x in ips if x}


def _same_ip(left: str, right: str) -> bool:
  """判断两个 IP/主机标识是否指向同一个地址。"""
  a = str(left or '').strip().lower()
  b = str(right or '').strip().lower()
  if not a or not b:
    return False
  if a == b:
    return True
  try:
    return ipaddress.ip_address(a) == ipaddress.ip_address(b)
  except Exception:
    return False


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
  raise HTTPException(status_code=400, detail=render_shell_error('local_only', server_ip=server_ip))


def _is_local_server_ip(server_ip: str) -> bool:
  """判断服务器 IP 是否为当前后端本机。

  参数：
  - server_ip：业务服务器 IP。

  返回：
  - True 表示本机；False 表示需要远端 SSH。
  """
  target = str(server_ip or '').strip()
  return any(_same_ip(target, ip) for ip in _list_local_ips())


async def _is_local_server_ip_async(server_ip: str) -> bool:
  """异步判断服务器 IP 是否为当前后端本机。"""
  target = str(server_ip or '').strip()
  return any(_same_ip(target, ip) for ip in await _list_local_ips_async())


async def _shell_command_exists(command_name: str) -> bool:
  """检查当前后端服务器是否存在某个命令。

  参数：
  - command_name：命令名称，例如 `sshpass`。

  作用：
  - 远端服务器有 root 密码时，需要 `sshpass` 做非交互 SSH。

  返回：
  - True 表示命令存在。
  """
  # 命令名来自后端固定调用，但仍做 quote，保持工具函数边界安全。
  safe_name = shlex.quote(str(command_name or '').strip())
  if not safe_name:
    return False
  code, _out, _err = await _run_shell(f'command -v {safe_name} >/dev/null 2>&1', timeout=5)
  return code == 0


def _build_ssh_askpass_command(password: str, ssh_opts: str, remote: str, quoted_command: str) -> str:
  """构建不依赖 sshpass 的密码 SSH 命令。

  参数：
  - password：服务器管理中保存的 root 密码。
  - ssh_opts：统一的 SSH 选项。
  - remote：SSH 目标，例如 `root@server-ip`。
  - quoted_command：已经 shell quote 后的远端命令。

  作用：
  - 当后端服务器没有安装 `sshpass` 时，使用 OpenSSH 原生的 `SSH_ASKPASS` 机制输入密码。
  - 临时 askpass 脚本只在单次命令执行期间存在，执行结束后由 trap 自动清理。

  返回：
  - 可交给 `_run_shell` 执行的 bash 命令字符串。
  """
  # OpenSSH 禁止从普通 stdin 读取密码，SSH_ASKPASS 可以在无 tty 场景下提供密码。
  # 这里生成一次性脚本，并通过 trap 在命令结束后删除。
  askpass_body = f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(password or ''))}\n"
  askpass_body_quoted = shlex.quote(askpass_body)
  return (
    f'askpass_script=$(mktemp {SSH_ASKPASS_TEMPLATE}) || exit 90; '
    'trap \'rm -f "$askpass_script"\' EXIT; '
    f'printf %s {askpass_body_quoted} > "$askpass_script"; '
    'chmod 700 "$askpass_script"; '
    'DISPLAY=pspm:0 SSH_ASKPASS="$askpass_script" SSH_ASKPASS_REQUIRE=force '
    f'setsid ssh -o NumberOfPasswordPrompts=1 {ssh_opts} {remote} {quoted_command} < /dev/null'
  )


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
  # server_row 来自服务器管理表，所有远程操作都必须先通过数据库记录拿到目标 IP。
  ip = str(getattr(server_row, 'ip', '') or '').strip()
  if not ip:
    return 2, '', render_shell_error('server_ip_required')
  # 如果目标就是后端本机，直接本地执行，避免要求本机也配置 SSH 登录自己。
  if await _is_local_server_ip_async(ip):
    return await _run_shell(command, timeout=timeout)

  if not SAFE_HOST_RE.match(ip):
    return 2, '', render_shell_error('server_ip_invalid', ip=ip)

  # 远端服务器统一走 root 账号；密码来自服务器管理菜单保存的配置。
  password = str(getattr(server_row, 'root_password', '') or '')
  # SSH_DEFAULT_OPTIONS 集中控制 StrictHostKeyChecking、连接超时等参数，避免各处硬编码。
  ssh_opts = SSH_DEFAULT_OPTIONS
  remote = f'root@{shlex.quote(ip)}'
  quoted_command = shlex.quote(command)

  if password:
    # 优先使用 sshpass；若后端未安装 sshpass，则回退到 SSH_ASKPASS 方案，避免强制远端安装额外工具。
    if await _shell_command_exists('sshpass'):
      shell_cmd = f'sshpass -p {shlex.quote(password)} ssh {ssh_opts} {remote} {quoted_command}'
    elif await _shell_command_exists('setsid'):
      shell_cmd = _build_ssh_askpass_command(password, ssh_opts, remote, quoted_command)
    else:
      return 127, '', render_shell_error('ssh_password_tool_missing')
  else:
    # 没有密码时按密钥登录处理，并启用 BatchMode，防止接口卡在交互式密码输入。
    shell_cmd = f'ssh {ssh_opts} {SSH_BATCH_MODE_OPTION} {remote} {quoted_command}'

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
    return False, render_shell_error('target_ip_required')
  if str(getattr(source_server_row, 'ip', '') or '').strip() == target:
    return True, PING_OK_MESSAGE
  if not SAFE_HOST_RE.match(target):
    return False, render_shell_error('target_ip_invalid', ip=target)
  code, out, err = await _run_server_shell(
    source_server_row,
    f'ping -c 1 -W 2 {shlex.quote(target)} >/dev/null 2>&1',
    timeout=15,
  )
  if code == 0:
    return True, PING_OK_MESSAGE
  return False, (err.strip() or out.strip() or render_shell_error('ping_failed'))


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
