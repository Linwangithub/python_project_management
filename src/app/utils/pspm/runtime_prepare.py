"""项目运行前置校验模块。

用途：
- 负责启动项目前的 Conda 初始化脚本探测、入口文件存在性检查和基础配置校验。
- runtime_utils.py 只保留启动/停止流程编排，降低运行态模块复杂度。
"""

from __future__ import annotations

import shlex

from fastapi import HTTPException

from app.utils.pspm.path_utils import _normalize_path, _safe_conda_name
from app.utils.pspm.conda_utils import detect_conda_init_on_server
from app.utils.pspm.runtime_helpers import (
  MSG_ENTRY_MISSING,
  _get_raw_start_command,
  _resolve_entry_file_abs_path,
)
from app.utils.pspm.shell_utils import _run_server_shell


async def _detect_remote_conda_init(server_row) -> str:
  """探测远程服务器可用的 Conda 初始化脚本。

  该函数保留原有入口名称，内部复用统一 Conda 工具，保证启动流程、同步流程和设置流程
  使用同一份候选路径配置。
  """
  return await detect_conda_init_on_server(server_row)


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


