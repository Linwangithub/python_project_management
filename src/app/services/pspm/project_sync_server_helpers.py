"""同步项目服务器访问辅助函数。

本模块只放同步项目流程中可复用的服务器查找、目录检测和文件检测逻辑。
拆出来的原因：
- `project_sync.py` 负责同步主流程；
- `project_sync_nginx.py` 负责同步已有 Nginx 配置；
- 两者都需要按服务器 IP 找当前用户可用服务器、检查远程文件/目录是否存在。

如果把这些函数放在任意一个业务模块里，另一个模块导入时容易形成循环导入。
"""

from __future__ import annotations

import shlex

from fastapi import HTTPException

from app.utils.pspm.shell_utils import (
  _find_server_row_by_ip,
  _list_allowed_server_rows,
  _run_server_shell,
)


async def _get_allowed_server_by_ip(session, current_user, server_ip: str):
  """按 IP 查询当前用户可使用的服务器记录。

  参数：
  - session：数据库会话，用于读取当前用户授权服务器列表。
  - current_user：当前登录用户，服务层通过它限制可访问服务器范围。
  - server_ip：前端选择的服务器 IP。

  作用：
  - 同步项目主流程、同步 Nginx 检测流程都需要先确定用户是否有目标服务器权限。
  - 统一在这里处理空 IP、无权限 IP 的错误提示，避免不同接口提示不一致。

  返回：
  - `(servers, server_row)`：当前用户可用服务器列表，以及匹配 `server_ip` 的服务器记录。
  """
  ip = str(server_ip or '').strip()
  if not ip:
    raise HTTPException(status_code=400, detail='服务器IP不能为空')

  # 读取当前用户可用服务器列表。普通用户只能看到绑定服务器，root 用户可看到全部服务器。
  servers = await _list_allowed_server_rows(session, current_user)

  # 在授权服务器列表里查找前端传入的 IP；没有命中说明用户无权操作这台服务器。
  server_row = _find_server_row_by_ip(servers, ip)
  if not server_row:
    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')
  return servers, server_row


async def _server_directory_exists(server_row, path: str) -> bool:
  """检查指定服务器上的目录是否存在。

  参数：
  - server_row：服务器记录，包含目标 IP、登录用户和凭据。
  - path：要检测的远程绝对目录。

  作用：
  - 同步项目目录选择、同步前最终确认都会用它确认项目目录真实存在。
  - 使用远程 `test -d`，避免把远程路径误判成本机路径。

  返回：
  - `True`：目录存在；`False`：目录不存在或命令返回非 0。
  """
  code, _out, _err = await _run_server_shell(server_row, f'test -d {shlex.quote(path)}', timeout=15)
  return code == 0


async def _server_file_exists(server_row, path: str) -> bool:
  """检查指定服务器上的文件是否存在。

  参数：
  - server_row：服务器记录，包含目标 IP、登录用户和凭据。
  - path：要检测的远程绝对文件路径。

  作用：
  - 同步项目入口文件、同步 Nginx 配置文件检测都会用它确认文件真实存在。
  - 使用远程 `test -f`，确保检测的是目标服务器上的文件，不是后端本机文件。

  返回：
  - `True`：文件存在；`False`：文件不存在或命令返回非 0。
  """
  code, _out, _err = await _run_server_shell(server_row, f'test -f {shlex.quote(path)}', timeout=15)
  return code == 0
