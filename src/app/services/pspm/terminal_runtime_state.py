"""终端启动项目后的运行状态持久化服务。

本模块负责写入远端 runtime pid/meta 文件，以及更新数据库中的项目运行状态。
WebSocket 会话层只负责检测端口和调用这里，不直接关心运行状态如何持久化。
"""

from __future__ import annotations

import shlex
import time

from app import crud, models
from app.core.database import get_session
from app.utils.pspm.path_utils import _safe_project_shell_script
from app.utils.pspm.project_config import PROJECT_RUNTIME_BASE_DIR
from app.utils.pspm.shell_utils import _run_server_shell


async def _write_project_runtime_meta(
    *,
    server_row: models.pspm.PspmServer,
    project_id: int,
    pid: str,
    port: str,
    mode: str,
) -> None:
    """前台服务端口就绪后写入运行 PID 和元信息文件。"""
    if not str(pid or '').isdigit():
        return
    runtime_dir = f'{PROJECT_RUNTIME_BASE_DIR}/project_{int(project_id)}'
    pid_file = f'{runtime_dir}/service.pid'
    meta_file = f'{runtime_dir}/service.meta'
    start_time_cmd = f"awk '{{print $22}}' /proc/{shlex.quote(str(pid))}/stat 2>/dev/null || true"
    code, start_time, _err = await _run_server_shell(server_row, start_time_cmd, timeout=10)
    start_time = (start_time or '').strip() if code == 0 else ''
    if not start_time:
        return
    started_at = int(time.time())
    meta_text = f'{pid}|{start_time}|{mode}|{started_at}|{port or ""}'
    script = f"""
set -euo pipefail
mkdir -p {shlex.quote(runtime_dir)}
printf '%s\n' {shlex.quote(str(pid))} > {shlex.quote(pid_file)}
printf '%s\n' {shlex.quote(meta_text)} > {shlex.quote(meta_file)}
"""
    await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=10)

async def _mark_project_running(project_id: int) -> None:
    """持久化项目已运行状态。"""
    async with get_session() as db:
        await crud.projects.update_status(db, project_id=project_id, running=True)

async def _mark_project_stopped(project_id: int) -> None:
    """持久化项目已停止状态。"""
    if not project_id:
        return
    async with get_session() as db:
        await crud.projects.update_status(db, project_id=project_id, running=False)
