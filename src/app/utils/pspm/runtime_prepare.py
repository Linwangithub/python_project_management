"""项目运行前置校验模块。

用途：
- 负责启动项目前的 Conda 初始化脚本探测、入口文件存在性检查和基础配置校验。
- runtime_utils.py 只保留启动/停止流程编排，降低运行态模块复杂度。
"""

from __future__ import annotations

import shlex

from fastapi import HTTPException

from app.utils.pspm.path_utils import _normalize_path, _safe_conda_name
from app.utils.pspm.project_config import CONDA_INIT_CANDIDATE_PATHS
from app.utils.pspm.runtime_helpers import (
  MSG_ENTRY_MISSING,
  _get_raw_start_command,
  _resolve_entry_file_abs_path,
)
from app.utils.pspm.shell_utils import _run_server_shell


async def _detect_remote_conda_init(server_row) -> str:
  """探测远程服务器可用的 Conda 初始化脚本。

  参数：
  - server_row：服务器记录，提供 IP、端口、密码等 SSH 连接信息。

  作用：
  - 项目前台、后台、部署启动前需要激活项目 Conda 环境。
  - 不同服务器 Conda 安装路径可能不同，因此按配置候选路径逐个检查。

  返回：
  - 可直接拼接到 shell 中执行的 source 命令。
  - 如果 conda 已在 PATH 中，则返回 true。

  异常：
  - 未找到 Conda 初始化方式时抛出 HTTP 400。
  """
  candidates = CONDA_INIT_CANDIDATE_PATHS
  checks = ' '.join(f'{shlex.quote(path)}' for path in candidates)
  command = (
    'for p in ' + checks + '; do '
    'if [ -f "$p" ]; then echo "$p"; exit 0; fi; '
    'done; '
    'command -v conda >/dev/null 2>&1 && echo "" && exit 0; '
    'exit 1'
  )
  code, out, _err = await _run_server_shell(server_row, command, timeout=15)
  if code != 0:
    raise HTTPException(status_code=400, detail='未找到Conda初始化脚本，无法激活项目Conda环境')
  path = (out or '').strip().splitlines()[0].strip() if (out or '').strip() else ''
  return f'source {shlex.quote(path)} >/dev/null 2>&1 || true' if path else 'true'


async def _ensure_remote_file_exists(server_row, abs_path: str, display_path: str) -> None:
  """确认远端服务器上指定入口文件存在。"""
  code, _out, _err = await _run_server_shell(server_row, f'test -f {shlex.quote(abs_path)}', timeout=15)
  if code != 0:
    raise HTTPException(status_code=400, detail=f'{MSG_ENTRY_MISSING}：{display_path}')


async def _ensure_project_runtime_config(server_row, project, mode: str) -> str:
  """校验项目启动所需的路径、Conda 环境名、入口文件和命令。

  返回：
  - 项目入口文件绝对路径。
  """
  _get_raw_start_command(project, mode)
  _normalize_path(project.backend_path or '')
  _safe_conda_name(project.conda_env_name or '')
  entry_abs_path, display_path = _resolve_entry_file_abs_path(project)
  await _ensure_remote_file_exists(server_row, entry_abs_path, display_path)
  return entry_abs_path


