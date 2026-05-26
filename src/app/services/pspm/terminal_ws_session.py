"""WebSocket 终端会话状态服务。

本模块维护可重连 WebSocket 终端的 PTY/SSH 进程、客户端列表、输出缓冲、
前台启动端口检测任务和关闭流程。接口层只负责 WebSocket 协议编排。
"""

from __future__ import annotations

import asyncio
import os
import pty
import select as select_module
import shlex
import signal
import subprocess
import uuid
from typing import Any, Dict

from fastapi import WebSocket, WebSocketException, status

from app import models, schemas
from app.core.database import get_session
from app.services.pspm.terminal_access import _get_allowed_server_by_ip
from app.services.pspm.terminal_runtime_state import _mark_project_running, _mark_project_stopped, _write_project_runtime_meta
from app.utils.pspm.path_utils import _safe_project_shell_script
from app.utils.pspm.project_config import TERMINAL_HOME_DIR, TERMINAL_WS_OUTPUT_BUFFER_LIMIT
from app.utils.pspm.shell_utils import _run_server_shell
from app.utils.pspm.terminal_config import (
    SHELL_MARK_PID,
    SHELL_MARK_READY,
    TERMINAL_DEFAULT_CONDA_ENV,
    WS_RESPONSE_CLOSED,
    WS_RESPONSE_FOREGROUND_PENDING,
    WS_RESPONSE_FOREGROUND_STARTED,
    WS_RESPONSE_OUTPUT,
    terminal_message,
)
from app.utils.pspm.terminal_shell import _build_terminal_process_command
from app.utils.pspm.terminal_ws_helpers import _extract_ws_marked_value, _safe_send_json, _terminal_ws_response

# WebSocket 终端默认 home 目录。
HOME_DIR = TERMINAL_HOME_DIR

# 每个 WebSocket 会话保留的输出缓冲条数上限。
WS_OUTPUT_BUFFER_LIMIT = TERMINAL_WS_OUTPUT_BUFFER_LIMIT

# WebSocket 终端会话内存存储，key 为 session_id。
_ws_terminal_sessions: Dict[str, Dict[str, Any]] = {}

# 保护 WebSocket 终端会话存储的异步锁。
_ws_terminal_lock = asyncio.Lock()


async def _find_ws_terminal_session_id_by_pid(user_id: int, pid: str) -> str:
    """根据 PTY/SSH 进程 PID 查找对应的 WebSocket 终端会话。"""
    target_pid = str(pid or '').strip()
    if not target_pid:
        return ''
    async with _ws_terminal_lock:
        for session_id, state in _ws_terminal_sessions.items():
            process = state.get('process')
            if state.get('user_id') == user_id and process and str(getattr(process, 'pid', '') or '') == target_pid:
                return session_id
    return ''

async def _get_ws_allowed_server_by_ip(current_user: schemas.users.Data, server_ip: str) -> models.pspm.PspmServer:
    """校验并加载当前 WebSocket 用户可使用的服务器。"""
    async with get_session() as db:
        return await _get_allowed_server_by_ip(db, current_user, server_ip)

async def _set_ws_foreground_state(session_id: str, project_id: int, pid: str, port: str) -> None:
    """记录前台终端会话绑定的项目、PID 和端口。"""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state:
            return
        state['foreground_project_id'] = int(project_id or 0)
        state['foreground_pid'] = str(pid or '').strip()
        state['foreground_port'] = str(port or '').strip()

async def _watch_foreground_port_ready(
    *,
    session_id: str,
    websocket: WebSocket,
    server_row: models.pspm.PspmServer,
    project_id: int,
    port: str,
    wait_seconds: int = 30,
) -> None:
    """等待前台服务监听端口，并把真实 PID/端口同步给前端和运行态文件。

    参数：
    - session_id：WebSocket 终端会话 ID，用于读取当前项目目录和 Conda 环境。
    - websocket：当前浏览器 WebSocket 连接，用于推送启动结果。
    - server_row：目标服务器记录，用于旁路执行 ss 和 /proc 检测命令。
    - project_id：项目 ID，用于写入独立 runtime 目录和更新数据库状态。
    - port：配置或命令中解析出的端口；为空时按项目目录自动发现真实监听端口。
    - wait_seconds：最多等待秒数。
    """
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        session_cwd = str(state.get('foreground_cwd') or HOME_DIR) if state else HOME_DIR
        session_conda_env = str(state.get('foreground_conda_env_name') or TERMINAL_DEFAULT_CONDA_ENV) if state else TERMINAL_DEFAULT_CONDA_ENV
    safe_port = shlex.quote(str(port or '').strip())
    safe_cwd = shlex.quote(session_cwd)
    script = f"""
expected_port={safe_port}
project_cwd={safe_cwd}
for _i in $(seq 1 {int(wait_seconds)}); do
  if [ -n "$expected_port" ]; then
    line="$(ss -lntpH 2>/dev/null | awk -v p="$expected_port" '$4 ~ ":"p"$" {{print; exit}}' || true)"
  else
    line="$(ss -lntpH 2>/dev/null | while IFS= read -r item; do
      pid="$(echo "$item" | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)"
      [ -n "$pid" ] || continue
      cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
      cmd="$(tr '\\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
      case "$cwd" in
        "$project_cwd"|"$project_cwd"/*) echo "$item"; break ;;
      esac
      case "$cmd" in
        *"$project_cwd"*) echo "$item"; break ;;
      esac
    done | head -n 1)"
  fi
  if [ -n "$line" ]; then
    pid="$(echo "$line" | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)"
    detected_port="$(echo "$line" | awk '{{print $4}}' | awk -F: '{{print $NF}}' | grep -E '^[0-9]+$' | head -n 1)"
    if [ -n "$pid" ] && [ -n "$detected_port" ]; then
      echo "{SHELL_MARK_READY}=1"
      echo "{SHELL_MARK_PID}=$pid"
      echo "PSPM_PORT=$detected_port"
      exit 0
    fi
  fi
  sleep 1
done
echo "{SHELL_MARK_READY}=0"
exit 23
"""
    code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=wait_seconds + 10)
    ready = _extract_ws_marked_value(out, SHELL_MARK_READY) == '1'
    pid = _extract_ws_marked_value(out, SHELL_MARK_PID)
    detected_port = _extract_ws_marked_value(out, 'PSPM_PORT') or str(port or '').strip()
    if ready and pid and detected_port:
        await _write_project_runtime_meta(server_row=server_row, project_id=project_id, pid=pid, port=detected_port, mode='dev')
        await _mark_project_running(project_id)
        await _set_ws_foreground_state(session_id, project_id, pid, detected_port)
        await _safe_send_json(websocket, _terminal_ws_response(WS_RESPONSE_FOREGROUND_STARTED, {
            'project_id': project_id,
            'pid': pid,
            'port': detected_port,
            'cwd': session_cwd,
            'conda_env_name': session_conda_env,
        }))
        return
    message = (err or out or '').strip() or '等待端口监听超时，请查看终端输出'
    await _safe_send_json(websocket, _terminal_ws_response(WS_RESPONSE_FOREGROUND_PENDING, {'message': message}))

async def _broadcast_ws_terminal_output(session_id: str, text: str) -> None:
    """把 PTY 输出写入会话缓冲区，并推送给当前已连接的浏览器客户端。

    参数：
    - session_id：后端 WebSocket 终端会话 ID，前端刷新后也会用它重连。
    - text：从 PTY 读取到的原始输出文本。

    作用：
    - 即使浏览器临时刷新，后台 reader 仍会持续读取 PTY，避免远程程序输出阻塞。
    - 最近输出会保存在内存缓冲区，便于后续扩展断线重放。
    """
    if not text:
        return
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state:
            return
        buffer = state.setdefault('output_buffer', [])
        buffer.append(text)
        if len(buffer) > WS_OUTPUT_BUFFER_LIMIT:
            del buffer[:-WS_OUTPUT_BUFFER_LIMIT]
        clients = list(state.get('clients') or [])

    dead_clients = []
    for client in clients:
        ok = await _safe_send_json(client, _terminal_ws_response(WS_RESPONSE_OUTPUT, {'text': text}))
        if not ok:
            dead_clients.append(client)

    if dead_clients:
        async with _ws_terminal_lock:
            state = _ws_terminal_sessions.get(session_id)
            if state:
                state['clients'] = [item for item in (state.get('clients') or []) if item not in dead_clients]

async def _ws_terminal_reader_loop(session_id: str) -> None:
    """持续读取某个后端终端会话的 PTY 输出。

    参数：
    - session_id：后端 WebSocket 终端会话 ID。

    作用：
    - reader 生命周期跟后端终端会话绑定，而不是跟某一个 WebSocket 连接绑定。
    - 页面刷新导致 WebSocket 短暂断开时，reader 不会停止，前台服务输出不会丢失或阻塞。
    """
    try:
        while True:
            async with _ws_terminal_lock:
                state = _ws_terminal_sessions.get(session_id)
                if not state:
                    break
                master_fd = state.get('master_fd')
                process = state.get('process')
            if master_fd is None:
                break
            try:
                ready, _w, _e = await asyncio.to_thread(select_module.select, [master_fd], [], [], 0.2)
                if not ready:
                    if process and process.poll() is not None:
                        break
                    continue
                data = os.read(master_fd, 4096)
                if not data:
                    break
                await _broadcast_ws_terminal_output(session_id, data.decode('utf-8', errors='replace'))
            except OSError:
                break
            except Exception as exc:
                await _broadcast_ws_terminal_output(session_id, f'\n读取终端输出失败：{exc}\n')
                break
    finally:
        async with _ws_terminal_lock:
            state = _ws_terminal_sessions.get(session_id)
            if state:
                state['reader_task'] = None
                state['alive'] = False

async def _create_ws_terminal_session(current_user: schemas.users.Data, server_ip: str, alias: str) -> Dict[str, Any]:
    """创建一个可重连的后端 WebSocket 终端会话。

    参数：
    - current_user：当前登录用户，用于权限和会话归属校验。
    - server_ip：业务服务器 IP。
    - alias：前端展示的终端标签名。

    返回：
    - Dict[str, Any]：包含 PTY、进程、业务服务器等信息的会话状态。
    """
    server_row = await _get_ws_allowed_server_by_ip(current_user, server_ip)
    command = await _build_terminal_process_command(server_row)
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        try:
            os.close(slave_fd)
        except Exception:
            pass
    if process is None:
        try:
            os.close(master_fd)
        except Exception:
            pass
        raise WebSocketException(code=status.WS_1011_INTERNAL_ERROR, reason='终端进程创建失败')

    session_id = uuid.uuid4().hex
    state: Dict[str, Any] = {
        'session_id': session_id,
        'user_id': current_user.id,
        'server_ip': server_ip,
        'alias': alias,
        'server_row': server_row,
        'process': process,
        'master_fd': master_fd,
        'clients': [],
        'watcher_tasks': [],
        'output_buffer': [],
        'reader_task': None,
        'alive': True,
        'cwd': HOME_DIR,
        'conda_env_name': 'base',
        'foreground_project_id': 0,
        'foreground_pid': '',
        'foreground_port': '',
    }
    async with _ws_terminal_lock:
        _ws_terminal_sessions[session_id] = state
        state['reader_task'] = asyncio.create_task(_ws_terminal_reader_loop(session_id))
    return state

async def _get_or_create_ws_terminal_session(
    current_user: schemas.users.Data,
    server_ip: str,
    alias: str,
    requested_session_id: str,
) -> tuple[Dict[str, Any], bool]:
    """按前端传入的 session_id 重连，找不到时创建新会话。

    参数：
    - current_user：当前登录用户。
    - server_ip：业务服务器 IP。
    - alias：终端标签。
    - requested_session_id：前端本地保存的后端终端会话 ID。

    返回：
    - tuple[Dict[str, Any], bool]：会话状态，以及是否为重连。
    """
    safe_session_id = str(requested_session_id or '').strip()
    if safe_session_id:
        async with _ws_terminal_lock:
            state = _ws_terminal_sessions.get(safe_session_id)
        if state and state.get('user_id') == current_user.id:
            process = state.get('process')
            if process and process.poll() is None:
                return state, True
            await _close_ws_terminal_session(safe_session_id, current_user.id)
    return await _create_ws_terminal_session(current_user, server_ip, alias), False

async def _attach_ws_terminal_client(session_id: str, websocket: WebSocket) -> None:
    """把当前 WebSocket 连接挂到后端终端会话上。"""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state:
            return
        clients = state.setdefault('clients', [])
        if websocket not in clients:
            clients.append(websocket)

async def _detach_ws_terminal_client(session_id: str, websocket: WebSocket) -> None:
    """把当前 WebSocket 连接从后端终端会话上摘除，但不关闭终端进程。"""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state:
            return
        state['clients'] = [item for item in (state.get('clients') or []) if item is not websocket]

async def _track_ws_watcher_task(session_id: str, task: asyncio.Task) -> None:
    """记录前台启动端口检测任务，避免 WebSocket 断开时任务被取消。"""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if state:
            state.setdefault('watcher_tasks', []).append(task)

    def _forget(done_task: asyncio.Task) -> None:
        """
        在端口检测任务结束后触发清理。
        
        参数：
        - done_task：已经结束的 watcher task。
        
        作用：
        - 作为 add_done_callback 的同步回调入口，转交给异步清理函数处理。
        """
        async def _remove() -> None:
            """
            从会话状态中移除已经结束的 watcher task。
            
            作用：
            - 避免前台启动端口检测任务完成后继续占用会话状态。
            """
            async with _ws_terminal_lock:
                state = _ws_terminal_sessions.get(session_id)
                if state:
                    state['watcher_tasks'] = [item for item in (state.get('watcher_tasks') or []) if item is not done_task]
        asyncio.create_task(_remove())

    task.add_done_callback(_forget)

async def _close_ws_terminal_session(session_id: str, user_id: int | None = None) -> bool:
    """显式关闭后端 WebSocket 终端会话。

    参数：
    - session_id：要关闭的后端终端会话 ID。
    - user_id：当前用户 ID；传入时会校验会话归属。

    作用：
    - 只有用户点击关闭终端窗口或调用关闭接口时才会执行。
    - 会关闭浏览器连接、取消 reader/watcher、杀掉 PTY/SSH 进程并释放 FD。
    """
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state or (user_id is not None and state.get('user_id') != user_id):
            return False
        _ws_terminal_sessions.pop(session_id, None)
        clients = list(state.get('clients') or [])
        watcher_tasks = list(state.get('watcher_tasks') or [])
        reader_task = state.get('reader_task')
        process = state.get('process')
        master_fd = state.get('master_fd')
        foreground_project_id = int(state.get('foreground_project_id') or 0)

    for client in clients:
        try:
            await client.send_json(_terminal_ws_response(WS_RESPONSE_CLOSED, {
                'message': '终端会话已关闭',
                'project_id': foreground_project_id,
            }))
            await client.close()
        except Exception:
            pass

    for task in watcher_tasks:
        if task and not task.done():
            task.cancel()
    if reader_task and not reader_task.done():
        reader_task.cancel()
        try:
            await reader_task
        except BaseException:
            pass

    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGHUP)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
    if master_fd is not None:
        try:
            os.close(master_fd)
        except Exception:
            pass
    if foreground_project_id:
        await _mark_project_stopped(foreground_project_id)
    return True
