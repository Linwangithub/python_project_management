"""Conda 初始化和命令执行工具。

本模块统一维护不同服务器上的 Conda 初始化脚本探测逻辑。对于 Conda 安装在
/home/<user> 下的 GPU 或个人服务器，命令会自动切换到对应 Linux 用户执行，避免
root 登录后找不到普通用户 Conda 环境。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from fastapi import HTTPException

from app.utils.pspm.project_config import CONDA_INIT_CANDIDATE_PATHS
from app.utils.pspm.shell_utils import _run_server_shell, _run_shell

CONDA_IN_PATH_MARKER = "__PSPM_CONDA_IN_PATH__"
CONDA_INIT_NOT_FOUND_MESSAGE = "未找到Conda初始化脚本，无法使用Conda环境"
CONDA_DEFAULT_ROOT_HOME = "/root"
CONDA_HOME_PREFIX = "/home/"
SAFE_LINUX_USER_RE = re.compile(r"^(root|[A-Za-z_][A-Za-z0-9_.-]*[$]?)$")


@dataclass(frozen=True)
class CondaExecutionContext:
  """单台服务器上的 Conda 执行上下文。

  字段：
  - init_path：探测到的 conda.sh 路径，或 PATH 标记。
  - init_command：可以直接执行的 Conda 初始化命令。
  - execution_user：应该执行 Conda 命令的 Linux 用户。
  - home_dir：execution_user 的 home 目录。
  - in_path：是否通过 PATH 直接找到 Conda。
  """

  init_path: str
  init_command: str
  execution_user: str
  home_dir: str
  in_path: bool = False


def _build_conda_init_detect_command() -> str:
  """构造 Conda 初始化脚本探测命令。

  返回：
  - shell 命令字符串，会按配置列表依次检查 `conda.sh` 是否存在。
  - 输出格式固定为 `路径<TAB>所属用户<TAB>用户home`，便于后续决定是否 `su - user`。
  """
  candidates = ' '.join(shlex.quote(path) for path in CONDA_INIT_CANDIDATE_PATHS)
  return (
    'for p in ' + candidates + '; do '
    'if [ -f "$p" ]; then '
    'owner="$(stat -c %U "$p" 2>/dev/null || true)"; '
    'if [ -z "$owner" ] || [ "$owner" = "UNKNOWN" ]; then owner="$(id -un)"; fi; '
    'home="$(getent passwd "$owner" 2>/dev/null | awk -F: \'{print $6}\')"; '
    'if [ -z "$home" ]; then if [ "$owner" = "root" ]; then home="/root"; else home="/home/$owner"; fi; fi; '
    'printf "%s\t%s\t%s\n" "$p" "$owner" "$home"; exit 0; '
    'fi; '
    'done; '
    'if command -v conda >/dev/null 2>&1; then '
    'owner="$(id -un)"; home="${HOME:-}"; '
    'if [ -z "$home" ]; then home="$(getent passwd "$owner" 2>/dev/null | awk -F: \'{print $6}\')"; fi; '
    'if [ -z "$home" ]; then if [ "$owner" = "root" ]; then home="/root"; else home="/home/$owner"; fi; fi; '
    f'printf "%s\t%s\t%s\n" {shlex.quote(CONDA_IN_PATH_MARKER)} "$owner" "$home"; exit 0; '
    'fi; '
    'exit 1'
  )


def _home_user_from_path(path: str) -> str:
  """从 `/home/<user>/...` 形式的路径中解析 Linux 用户名。"""
  value = str(path or '').strip()
  if not value.startswith(CONDA_HOME_PREFIX):
    return ''
  parts = value.split('/')
  return parts[2] if len(parts) > 2 else ''


def _safe_execution_user(value: str) -> str:
  """校验远端返回的 Linux 用户名，异常时回退 root。"""
  user = str(value or '').strip() or 'root'
  if not SAFE_LINUX_USER_RE.match(user):
    return 'root'
  return user


def _fallback_home_for_user(user: str) -> str:
  """根据 Linux 用户名生成兜底 home 目录。"""
  return CONDA_DEFAULT_ROOT_HOME if user == 'root' else f'/home/{user}'


def _parse_conda_detect_output(output: str) -> CondaExecutionContext:
  """把探测命令输出解析为 CondaExecutionContext。"""
  line = (output or '').strip().splitlines()[0].strip() if (output or '').strip() else ''
  if not line:
    raise HTTPException(status_code=400, detail=CONDA_INIT_NOT_FOUND_MESSAGE)

  parts = line.split('\t')
  detected_path = parts[0].strip() if parts else ''
  owner = parts[1].strip() if len(parts) > 1 else 'root'
  detected_home = parts[2].strip() if len(parts) > 2 else ''

  if detected_path == CONDA_IN_PATH_MARKER:
    user = _safe_execution_user(owner)
    return CondaExecutionContext(
      init_path=detected_path,
      init_command='true',
      execution_user=user,
      home_dir=detected_home or _fallback_home_for_user(user),
      in_path=True,
    )

  if not detected_path:
    raise HTTPException(status_code=400, detail=CONDA_INIT_NOT_FOUND_MESSAGE)

  # Conda 安装在 /home/<user> 下时，优先使用路径中的用户，而不是当前 SSH 登录用户。
  user = _safe_execution_user(_home_user_from_path(detected_path) or owner)
  owner_user = _safe_execution_user(owner)
  home_dir = detected_home if user == owner_user and detected_home else _fallback_home_for_user(user)
  return CondaExecutionContext(
    init_path=detected_path,
    init_command=f'source {shlex.quote(detected_path)} >/dev/null 2>&1 || true',
    execution_user=user,
    home_dir=home_dir,
    in_path=False,
  )


def context_needs_user_switch(context: CondaExecutionContext) -> bool:
  """判断 Conda 命令是否需要从 root 切换到普通用户执行。"""
  return bool(context.execution_user and context.execution_user != 'root')


def build_conda_context_shell_command(context: CondaExecutionContext, script: str) -> str:
  """把业务脚本包装到 Conda 所属用户上下文中执行。

  参数：
  - context：`detect_conda_context_on_server` 返回的上下文。
  - script：真正要执行的 bash 脚本。

  返回：
  - root Conda 直接返回 `bash -lc ...`。
  - 普通用户 Conda 返回 `su - user -c 'bash -lc ...'`。
  """
  inner = f'bash -lc {shlex.quote(str(script or ""))}'
  if not context_needs_user_switch(context):
    return inner
  return f'su - {shlex.quote(context.execution_user)} -c {shlex.quote(inner)}'


async def detect_conda_context_on_server(server_row, timeout: int = 15) -> CondaExecutionContext:
  """探测指定服务器可用的 Conda 初始化脚本和执行用户。"""
  code, out, err = await _run_server_shell(server_row, _build_conda_init_detect_command(), timeout=timeout)
  if code != 0:
    message = (err or '').strip() or (out or '').strip() or CONDA_INIT_NOT_FOUND_MESSAGE
    raise HTTPException(status_code=400, detail=message if message else CONDA_INIT_NOT_FOUND_MESSAGE)
  return _parse_conda_detect_output(out)


async def detect_conda_context_on_local(timeout: int = 15) -> CondaExecutionContext:
  """探测当前后端服务器本机可用的 Conda 初始化脚本和执行用户。"""
  code, out, err = await _run_shell(_build_conda_init_detect_command(), timeout=timeout)
  if code != 0:
    message = (err or '').strip() or (out or '').strip() or CONDA_INIT_NOT_FOUND_MESSAGE
    raise HTTPException(status_code=400, detail=message if message else CONDA_INIT_NOT_FOUND_MESSAGE)
  return _parse_conda_detect_output(out)


async def detect_conda_init_on_server(server_row, timeout: int = 15) -> str:
  """兼容旧调用：只返回 Conda 初始化命令。"""
  return (await detect_conda_context_on_server(server_row, timeout=timeout)).init_command


async def detect_conda_init_on_local(timeout: int = 15) -> str:
  """兼容旧调用：只返回当前后端服务器本机 Conda 初始化命令。"""
  return (await detect_conda_context_on_local(timeout=timeout)).init_command


async def run_shell_in_conda_context_on_server(
  server_row,
  command: str,
  timeout: int = 120,
  include_conda_init: bool = True,
) -> tuple[int, str, str]:
  """在目标服务器的 Conda 所属用户上下文中执行 shell 命令。"""
  context = await detect_conda_context_on_server(server_row)
  script = str(command or '')
  if include_conda_init:
    script = f'{context.init_command}; {script}'
  return await _run_server_shell(server_row, build_conda_context_shell_command(context, script), timeout=timeout)


async def run_conda_command_on_server(server_row, command: str, timeout: int = 120) -> tuple[int, str, str]:
  """在指定服务器初始化 Conda 后执行命令。"""
  return await run_shell_in_conda_context_on_server(server_row, command, timeout=timeout, include_conda_init=True)


async def run_conda_command_local(command: str, timeout: int = 120) -> tuple[int, str, str]:
  """在当前后端服务器初始化 Conda 后执行命令。"""
  context = await detect_conda_context_on_local()
  script = f'{context.init_command}; {command}'
  return await _run_shell(build_conda_context_shell_command(context, script), timeout=timeout)
