from __future__ import annotations

import asyncio
import json
import os
import pty
import re
import select as select_module
import shlex
import signal
import subprocess
import uuid
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, WebSocketException, status
from sqlalchemy import select

from app import crud, models, schemas
from app.api.deps import CurrentWSUser, require_permission
from app.core.database import get_session
from app.core.deps import SessionDep
from app.utils.pspm.path_utils import _safe_project_shell_script
from app.utils.pspm.shell_utils import _is_local_server_ip_async, _run_server_shell, _run_shell

router = APIRouter()

HOME_DIR = '/root'
DEFAULT_HOST_LABEL = 'wcp'
COMMAND_TIMEOUT_SECONDS = 30
CONDA_INIT = 'source /root/miniforge3/etc/profile.d/conda.sh >/dev/null 2>&1 || true; '

_terminal_sessions: Dict[str, Dict[str, Any]] = {}
_terminal_lock = asyncio.Lock()

ANSI_PATTERN = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def _format_prompt(host_label: str, cwd: str) -> str:
    """生成终端提示符文本。

    参数：
    - host_label：终端标签中展示的主机别名。
    - cwd：当前会话所在工作目录。

    返回：
    - str：形如 `(base) [root@host ~]#` 的提示符。
    """
    if cwd == HOME_DIR:
        display_path = '~'
    elif cwd.startswith(f'{HOME_DIR}/'):
        display_path = f'~{cwd[len(HOME_DIR):]}'
    else:
        display_path = cwd
    return f'(base) [root@{host_label} {display_path}]#'



def _format_prompt_with_env(host_label: str, cwd: str, conda_env: str | None = None) -> str:
    """根据会话保存的 Conda 环境生成提示符。"""
    env_name = (conda_env or '').strip() or 'base'
    if cwd == HOME_DIR:
        display_path = '~'
    elif cwd.startswith(f'{HOME_DIR}/'):
        display_path = f'~{cwd[len(HOME_DIR):]}'
    else:
        display_path = cwd
    return f'({env_name}) [root@{host_label} {display_path}]#'


def _normalize_cwd(cwd: str | None) -> str:
    """规范化终端会话工作目录。

    参数：
    - cwd：会话中保存的目录，可能为空。

    返回：
    - str：绝对路径形式的工作目录。
    """
    path = (cwd or '').strip() or HOME_DIR
    path = os.path.normpath(path)
    if not path.startswith('/'):
        path = f'/{path}'
    return path


def _resolve_path(current_cwd: str, target: str | None) -> str:
    """把 cd 命令目标转换为绝对路径。

    参数：
    - current_cwd：当前会话目录。
    - target：用户输入的目标路径。

    返回：
    - str：解析后的绝对路径。
    """
    raw = (target or '').strip()
    if not raw or raw == '~':
        return HOME_DIR

    if raw.startswith('~/'):
        candidate = f"{HOME_DIR}/{raw[2:]}"
    elif raw.startswith('/'):
        candidate = raw
    else:
        candidate = f"{current_cwd.rstrip('/')}/{raw}"

    normalized = os.path.normpath(candidate)
    if not normalized.startswith('/'):
        normalized = f'/{normalized}'
    return normalized


def _split_command(command: str) -> List[str]:
    """安全拆分终端命令参数。

    参数：
    - command：用户在终端输入框提交的命令。

    返回：
    - List[str]：按 shell 规则拆分后的参数列表。
    """
    try:
        return shlex.split(command)
    except ValueError:
        raise HTTPException(status_code=400, detail='命令格式不正确，请检查引号')


def _extract_path_token(command: str) -> Tuple[str, str]:
    """提取命令中正在补全的最后一个路径片段。

    参数：
    - command：用户当前输入的完整命令。

    返回：
    - Tuple[str, str]：最后片段前缀和待补全 token。
    """
    raw = str(command or '')
    if not raw.strip():
        return raw, ''
    if raw.endswith(' '):
        return raw, ''
    token = raw.split()[-1]
    prefix = raw[:-len(token)] if token else raw
    return prefix, token


def _resolve_completion_base(cwd: str, token: str) -> Tuple[str, str]:
    """计算路径自动补全需要扫描的目录和文件名前缀。

    参数：
    - cwd：当前会话目录。
    - token：待补全路径片段。

    返回：
    - Tuple[str, str]：基础目录和文件名前缀。
    """
    if token.startswith('~/'):
        abs_target = f"{HOME_DIR}/{token[2:]}"
        display_prefix = '~/'
    elif token.startswith('/'):
        abs_target = token
        display_prefix = '/'
    elif token.startswith('~'):
        abs_target = HOME_DIR
        display_prefix = '~'
    else:
        abs_target = os.path.join(cwd, token)
        display_prefix = ''

    normalized = os.path.normpath(abs_target)
    if token.endswith('/') and not normalized.endswith('/'):
        normalized = f'{normalized}/'

    base_dir = normalized if normalized.endswith('/') else os.path.dirname(normalized)
    base_name = '' if normalized.endswith('/') else os.path.basename(normalized)

    if not base_dir:
        base_dir = '/'
    return os.path.normpath(base_dir), base_name


def _to_display_path(abs_path: str) -> str:
    """把绝对路径转换为终端中更友好的展示路径。

    参数：
    - abs_path：候选文件或目录的绝对路径。

    返回：
    - str：以 `~` 简写后的展示路径。
    """
    normalized = os.path.normpath(abs_path)
    if normalized == HOME_DIR:
        return '~'
    if normalized.startswith(f'{HOME_DIR}/'):
        return f"~/{normalized[len(HOME_DIR)+1:]}"
    return normalized


def _complete_path_candidates(cwd: str, token: str) -> List[str]:
    """列出路径自动补全候选项。

    参数：
    - cwd：当前会话目录。
    - token：待补全路径片段。

    返回：
    - List[str]：可选路径候选列表。
    """
    base_dir, base_name = _resolve_completion_base(cwd, token)
    if not os.path.isdir(base_dir):
        return []

    candidates: List[str] = []
    try:
        for entry in os.scandir(base_dir):
            name = entry.name
            if not name.startswith(base_name):
                continue
            abs_candidate = os.path.join(base_dir, name)
            display = _to_display_path(abs_candidate)
            if entry.is_dir():
                display = f'{display}/'
            candidates.append(display)
    except Exception:
        return []
    return sorted(set(candidates))


def _common_prefix(values: List[str]) -> str:
    """计算多个补全候选项的公共前缀。

    参数：
    - values：候选字符串列表。

    返回：
    - str：所有候选项共有的最长前缀。
    """
    if not values:
        return ''
    prefix = values[0]
    for value in values[1:]:
        i = 0
        limit = min(len(prefix), len(value))
        while i < limit and prefix[i] == value[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            break
    return prefix


def _is_command_token(prefix: str, token: str) -> bool:
    """判断当前 token 是否应该按命令名补全。

    参数：
    - prefix：token 前面的命令内容。
    - token：待补全片段。

    返回：
    - bool：True 表示补全命令名，False 表示补全路径。
    """
    if not token:
        return False
    if prefix.strip():
        return False
    if token.startswith(('~', '.', '/')):
        return False
    if '/' in token:
        return False
    return True


async def _complete_command_candidates(token: str) -> List[str]:
    """通过 bash compgen 查询命令名补全候选项。

    参数：
    - token：待补全命令名前缀。

    返回：
    - List[str]：命令名候选列表。
    """
    if not token:
        return []

    cmd = f"{CONDA_INIT}compgen -c -- {shlex.quote(token)} | sort -u"
    process = await asyncio.create_subprocess_exec(
        '/bin/bash',
        '-lc',
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    timed_out = False
    try:
        stdout_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, _ = await process.communicate()

    if timed_out:
        return []

    lines = (stdout_bytes or b'').decode('utf-8', errors='replace').splitlines()
    values = []
    for line in lines:
        item = (line or '').strip()
        if not item:
            continue
        if not item.startswith(token):
            continue
        values.append(item)

    return sorted(set(values))


async def _list_allowed_servers(session: SessionDep, current_user: schemas.users.Data) -> List[models.pspm.PspmServer]:
    """查询当前用户可创建终端会话的服务器列表。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户。

    返回：
    - List[PspmServer]：当前用户可使用的服务器记录。
    """
    is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)

    if is_root:
        stmt = (
            select(models.pspm.PspmServer)
            .where(models.pspm.PspmServer.status != -1)
            .order_by(models.pspm.PspmServer.id.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    stmt = (
        select(models.pspm.PspmServer)
        .where(models.pspm.PspmServer.status != -1)
        .order_by(models.pspm.PspmServer.id.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    username = (current_user.username or '').strip()
    if not username:
        return []
    return [
        row for row in rows
        if username in [x for x in crud.pspm.normalize_assigned_users(row.assigned_users).split(',') if x]
    ]


async def _get_allowed_server_by_ip(
    session: SessionDep,
    current_user: schemas.users.Data,
    server_ip: str,
) -> models.pspm.PspmServer:
    """按 IP 查找当前用户可使用的服务器。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户。
    - server_ip：前端选择的服务器 IP。

    返回：
    - PspmServer：匹配到的服务器记录。
    """
    ip = (server_ip or '').strip()
    if not ip:
        raise HTTPException(status_code=400, detail='服务器IP不能为空')

    candidates = await _list_allowed_servers(session, current_user)
    for server in candidates:
        if server.ip == ip:
            return server

    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')



async def _get_allowed_server_by_id(
    session: SessionDep,
    current_user: schemas.users.Data,
    server_id: int | None,
) -> models.pspm.PspmServer:
    """按服务器 ID 查找当前用户可使用的服务器。"""
    if not server_id:
        raise HTTPException(status_code=400, detail='终端会话缺少服务器ID')

    candidates = await _list_allowed_servers(session, current_user)
    for server in candidates:
        if int(server.id) == int(server_id):
            return server

    raise HTTPException(status_code=403, detail='当前用户无该服务器使用权限')


async def _get_session_server_row(
    session: SessionDep,
    current_user: schemas.users.Data,
    session_data: Dict[str, Any],
) -> models.pspm.PspmServer:
    """根据终端会话中保存的 server_id/server_ip 获取真实目标服务器。"""
    server_id = session_data.get('server_id')
    if server_id:
        return await _get_allowed_server_by_id(session, current_user, int(server_id))
    return await _get_allowed_server_by_ip(session, current_user, str(session_data.get('server_ip') or ''))


def _wrap_remote_bash(script: str) -> str:
    """把脚本包装成可交给目标服务器执行的 bash -lc 命令。"""
    return f'bash -lc {shlex.quote(script)}'


async def _remote_path_is_dir(server_row, path: str) -> bool:
    """在终端会话对应服务器上判断目录是否存在。"""
    safe_path = shlex.quote(_normalize_cwd(path))
    code, _out, _err = await _run_server_shell(server_row, _wrap_remote_bash(f'test -d {safe_path}'), timeout=10)
    return code == 0


async def _run_terminal_command_on_server(
    server_row,
    command: str,
    cwd: str,
    timeout: int,
    conda_env_name: str = 'base',
    detach: bool = False,
) -> tuple[int, str, str]:
    """在终端会话对应服务器的指定目录和 Conda 环境中执行命令。"""
    safe_cwd = shlex.quote(_normalize_cwd(cwd))
    env_name = (conda_env_name or 'base').strip() or 'base'
    activate = '' if env_name == 'base' else f'conda activate {shlex.quote(env_name)} >/dev/null 2>&1 && '
    if detach:
        script = (
            f'cd {safe_cwd} && {CONDA_INIT}{activate}'
            f'log_file=$(mktemp /tmp/pspm_terminal_fg_XXXXXX.log); '
            f': > "$log_file"; '
            f'({command}) >> "$log_file" 2>&1 & '
            f'pid=$!; '
            f'echo "PSPM_PID=$pid"; '
            f'echo "PSPM_LOG=$log_file"; '
            f'sleep 1; '
            f'echo "PSPM_LOG_BEGIN"; tail -n 80 "$log_file" 2>/dev/null || true; echo "PSPM_LOG_END"'
        )
    else:
        script = f'cd {safe_cwd} && {CONDA_INIT}{activate}{command}'
    return await _run_server_shell(server_row, _wrap_remote_bash(script), timeout=timeout)


async def _complete_command_candidates_on_server(server_row, token: str) -> List[str]:
    """在终端会话对应服务器上查询命令名补全候选项。"""
    if not token:
        return []

    script = f'{CONDA_INIT}compgen -c -- {shlex.quote(token)} | sort -u'
    code, out, _err = await _run_server_shell(server_row, _wrap_remote_bash(script), timeout=10)
    if code != 0:
        return []

    values = []
    for line in (out or '').splitlines():
        item = (line or '').strip()
        if item and item.startswith(token):
            values.append(item)
    return sorted(set(values))


async def _complete_path_candidates_on_server(server_row, cwd: str, token: str) -> List[str]:
    """在终端会话对应服务器上查询路径补全候选项。"""
    base_dir, base_name = _resolve_completion_base(cwd, token)
    script = (
        f'base={shlex.quote(base_dir)}; '
        'if [ -d "$base" ]; then '
        'find "$base" -maxdepth 1 -mindepth 1 -printf "%p\\t%y\\n" 2>/dev/null; '
        'fi'
    )
    code, out, _err = await _run_server_shell(server_row, _wrap_remote_bash(script), timeout=10)
    if code != 0:
        return []

    candidates: List[str] = []
    for line in (out or '').splitlines():
        raw = (line or '').strip()
        if not raw or '\t' not in raw:
            continue
        abs_path, kind = raw.rsplit('\t', 1)
        name = os.path.basename(abs_path.rstrip('/'))
        if not name.startswith(base_name):
            continue
        display = _to_display_path(abs_path)
        if kind == 'd' and not display.endswith('/'):
            display = f'{display}/'
        candidates.append(display)
    return sorted(set(candidates))


async def _set_session_data(session_id: str, data: Dict[str, Any]) -> None:
    """写入内存终端会话数据。

    参数：
    - session_id：终端会话唯一 ID。
    - data：需要保存的会话上下文。

    返回：
    - None。
    """
    async with _terminal_lock:
        _terminal_sessions[session_id] = data


async def _get_session_data(session_id: str, user_id: int) -> Dict[str, Any]:
    """读取并校验当前用户的终端会话数据。

    参数：
    - session_id：终端会话唯一 ID。
    - user_id：当前登录用户 ID。

    返回：
    - Dict[str, Any]：会话上下文数据。
    """
    async with _terminal_lock:
        data = _terminal_sessions.get(session_id)

    if not data or data.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='会话不存在')
    return data


async def _update_session_cwd(session_id: str, user_id: int, cwd: str) -> Dict[str, Any]:
    """更新终端会话当前工作目录。

    参数：
    - session_id：终端会话唯一 ID。
    - user_id：当前登录用户 ID。
    - cwd：新的工作目录。

    返回：
    - Dict[str, Any]：更新后的会话上下文。
    """
    async with _terminal_lock:
        data = _terminal_sessions.get(session_id)
        if not data or data.get('user_id') != user_id:
            raise HTTPException(status_code=404, detail='会话不存在')
        data['cwd'] = cwd
        return dict(data)


async def _remove_session(session_id: str, user_id: int) -> bool:
    """关闭并移除内存终端会话。

    参数：
    - session_id：终端会话唯一 ID。
    - user_id：当前登录用户 ID。

    返回：
    - bool：是否成功移除。
    """
    async with _terminal_lock:
        data = _terminal_sessions.get(session_id)
        if not data or data.get('user_id') != user_id:
            return False
        _terminal_sessions.pop(session_id, None)
        return True




def _terminal_ws_response(message: str, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build a JSON message for the terminal WebSocket."""
    return {'type': message, 'data': data or {}}


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return ANSI_PATTERN.sub('', str(text or ''))


def _extract_ws_marked_value(output: str, key: str) -> str:
    """Extract KEY=value from shell output."""
    prefix = f'{key}='
    for line in str(output or '').splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ''


def _build_sshpass_prefix(password: str) -> str:
    """Build an sshpass prefix for one SSH process."""
    if not password:
        return ''
    safe_password = shlex.quote(str(password))
    return f"sshpass -p {safe_password} "


def _build_askpass_ssh_command(password: str, ssh_command: str) -> str:
    """Build an SSH command that uses SSH_ASKPASS when sshpass is absent."""
    askpass_body = f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(password or ''))}\n"
    askpass_body_quoted = shlex.quote(askpass_body)
    ssh_command_quoted = shlex.quote(ssh_command)
    return (
        'askpass_script=$(mktemp /tmp/pspm_ws_askpass_XXXXXX) || exit 90; '
        'trap \'rm -f "$askpass_script"\' EXIT; '
        f'printf %s {askpass_body_quoted} > "$askpass_script"; '
        'chmod 700 "$askpass_script"; '
        f'DISPLAY=pspm:0 SSH_ASKPASS="$askpass_script" SSH_ASKPASS_REQUIRE=force setsid bash -lc {ssh_command_quoted}'
    )


async def _build_terminal_process_command(server_row: models.pspm.PspmServer) -> List[str]:
    """Build the local PTY command for a local shell or an interactive SSH shell."""
    ip = str(getattr(server_row, 'ip', '') or '').strip()
    if await _is_local_server_ip_async(ip):
        return ['/bin/bash', '-l']

    if not re.match(r'^[A-Za-z0-9_.:-]+$', ip):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=f'\u670d\u52a1\u5668IP\u683c\u5f0f\u4e0d\u5408\u6cd5\uff1a{ip}')

    ssh_port = int(getattr(server_row, 'ssh_port', 22) or 22)
    password = str(getattr(server_row, 'root_password', '') or '')
    base_ssh = (
        f"ssh -tt -p {ssh_port} "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=8 "
        "-o LogLevel=ERROR "
        "-o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=2 "
        f"root@{shlex.quote(ip)}"
    )

    has_sshpass = (await _run_shell('command -v sshpass >/dev/null 2>&1', timeout=5))[0] == 0
    if password and has_sshpass:
        return ['/bin/bash', '-lc', f'{_build_sshpass_prefix(password)}{base_ssh}']

    has_setsid = (await _run_shell('command -v setsid >/dev/null 2>&1', timeout=5))[0] == 0
    if password and has_setsid:
        return ['/bin/bash', '-lc', _build_askpass_ssh_command(password, base_ssh)]

    if password:
        raise WebSocketException(code=status.WS_1011_INTERNAL_ERROR, reason='\u5f53\u524d\u540e\u7aef\u7f3a\u5c11 sshpass/setsid\uff0c\u65e0\u6cd5\u521b\u5efa\u5bc6\u7801 SSH \u7ec8\u7aef')
    return ['/bin/bash', '-lc', base_ssh]


async def _get_ws_allowed_server_by_ip(current_user: schemas.users.Data, server_ip: str) -> models.pspm.PspmServer:
    """Validate and load a server for the current WebSocket user."""
    async with get_session() as db:
        return await _get_allowed_server_by_ip(db, current_user, server_ip)


async def _write_project_runtime_meta(
    *,
    server_row: models.pspm.PspmServer,
    project_id: int,
    pid: str,
    port: str,
    mode: str,
) -> None:
    """Write runtime pid/meta files after a foreground service becomes ready."""
    if not str(pid or '').isdigit():
        return
    import time
    runtime_dir = f'/tmp/pspm/runtime/project_{int(project_id)}'
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
    """Persist that a project is running."""
    async with get_session() as db:
        await crud.projects.update_status(db, project_id=project_id, running=True)


async def _safe_send_json(websocket: WebSocket, payload: Dict[str, Any]) -> bool:
    """Send a JSON message and return False if the socket is already closed."""
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False


def _write_pty(master_fd: int, text: str) -> None:
    """Write browser input into the PTY master."""
    os.write(master_fd, str(text or '').encode('utf-8', errors='replace'))


async def _watch_foreground_port_ready(
    *,
    websocket: WebSocket,
    server_row: models.pspm.PspmServer,
    project_id: int,
    port: str,
    wait_seconds: int = 30,
) -> None:
    """Use a side shell to detect when the foreground service starts listening."""
    safe_port = shlex.quote(str(port or '').strip())
    if not str(port or '').strip():
        await _safe_send_json(websocket, _terminal_ws_response('foreground_pending', {'message': '\u542f\u52a8\u547d\u4ee4\u5df2\u8fdb\u5165\u524d\u53f0\u8fd0\u884c\uff0c\u672a\u914d\u7f6e\u7aef\u53e3\uff0c\u8bf7\u4ee5\u7ec8\u7aef\u8f93\u51fa\u4e3a\u51c6'}))
        return
    script = f"""
port={safe_port}
for _i in $(seq 1 {int(wait_seconds)}); do
  line="$(ss -lntpH 2>/dev/null | awk -v p="$port" '$4 ~ ":"p"$" {{print; exit}}' || true)"
  if [ -n "$line" ]; then
    pid="$(printf '%s\n' "$line" | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' | head -n 1)"
    echo "PSPM_READY=1"
    echo "PSPM_PID=$pid"
    exit 0
  fi
  sleep 1
done
echo "PSPM_READY=0"
exit 23
"""
    code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=wait_seconds + 10)
    ready = _extract_ws_marked_value(out, 'PSPM_READY') == '1'
    pid = _extract_ws_marked_value(out, 'PSPM_PID')
    if ready and pid:
        await _write_project_runtime_meta(server_row=server_row, project_id=project_id, pid=pid, port=port, mode='dev')
        await _mark_project_running(project_id)
        await _safe_send_json(websocket, _terminal_ws_response('foreground_started', {
            'project_id': project_id,
            'pid': pid,
            'port': port,
        }))
        return
    message = (err or out or '').strip() or '\u7b49\u5f85\u7aef\u53e3\u76d1\u542c\u8d85\u65f6\uff0c\u8bf7\u67e5\u770b\u7ec8\u7aef\u8f93\u51fa'
    await _safe_send_json(websocket, _terminal_ws_response('foreground_pending', {'message': message}))


@router.websocket('/ws', name='pspm_terminal_ws')
async def terminal_websocket(
    websocket: WebSocket,
    current_user: CurrentWSUser,
):
    """Interactive terminal implemented with one isolated WebSocket + PTY per tab."""
    await websocket.accept(subprotocol='pspm-terminal')
    process: subprocess.Popen | None = None
    master_fd: int | None = None
    reader_task: asyncio.Task | None = None
    watcher_task: asyncio.Task | None = None
    server_row: models.pspm.PspmServer | None = None

    async def reader_loop() -> None:
        """Read PTY output and stream it back to the browser."""
        assert master_fd is not None
        while True:
            try:
                ready, _w, _e = await asyncio.to_thread(select_module.select, [master_fd], [], [], 0.2)
                if not ready:
                    if process and process.poll() is not None:
                        break
                    continue
                data = os.read(master_fd, 4096)
                if not data:
                    break
                text = data.decode('utf-8', errors='replace')
                ok = await _safe_send_json(websocket, _terminal_ws_response('output', {'text': text}))
                if not ok:
                    break
            except OSError:
                break
            except Exception as exc:
                await _safe_send_json(websocket, _terminal_ws_response('error', {'message': f'\u8bfb\u53d6\u7ec8\u7aef\u8f93\u51fa\u5931\u8d25\uff1a{exc}'}))
                break

    try:
        first = await websocket.receive_json()
        if first.get('type') != 'open':
            await websocket.send_json(_terminal_ws_response('error', {'message': '\u7ec8\u7aef\u9996\u5305\u5fc5\u987b\u662f open'}))
            return
        server_ip = str(first.get('server_ip') or '').strip()
        alias = str(first.get('alias') or '').strip() or 'terminal'
        if not server_ip:
            await websocket.send_json(_terminal_ws_response('error', {'message': '\u670d\u52a1\u5668IP\u4e0d\u80fd\u4e3a\u7a7a'}))
            return

        server_row = await _get_ws_allowed_server_by_ip(current_user, server_ip)
        command = await _build_terminal_process_command(server_row)
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        reader_task = asyncio.create_task(reader_loop())
        await websocket.send_json(_terminal_ws_response('ready', {
            'session_id': uuid.uuid4().hex,
            'server_ip': server_ip,
            'alias': alias,
            'pid': process.pid,
        }))

        while True:
            data = await websocket.receive_json()
            msg_type = str(data.get('type') or '').strip()
            if msg_type == 'input':
                text = str(data.get('text') or '')
                if master_fd is not None:
                    _write_pty(master_fd, text)
            elif msg_type == 'run_foreground':
                project_id = int(data.get('project_id') or 0)
                port = str(data.get('port') or '').strip()
                work_dir = str(data.get('work_dir') or '').strip()
                conda_env_name = str(data.get('conda_env_name') or '').strip()
                command_text = str(data.get('command') or '').strip()
                if not command_text:
                    await websocket.send_json(_terminal_ws_response('error', {'message': '\u6682\u65e0\u914d\u7f6e\u542f\u52a8\u547d\u4ee4'}))
                    continue
                if master_fd is not None:
                    if work_dir:
                        _write_pty(master_fd, f"cd {shlex.quote(work_dir)}\n")
                        await asyncio.sleep(0.2)
                    if conda_env_name:
                        _write_pty(master_fd, f"conda activate {shlex.quote(conda_env_name)}\n")
                        await asyncio.sleep(0.2)
                    _write_pty(master_fd, f"{command_text}\n")
                    await websocket.send_json(_terminal_ws_response('foreground_pending', {'message': '\u542f\u52a8\u547d\u4ee4\u5df2\u8fdb\u5165\u524d\u53f0\u8fd0\u884c\uff0c\u6b63\u5728\u7b49\u5f85\u7aef\u53e3\u76d1\u542c'}))
                    if watcher_task and not watcher_task.done():
                        watcher_task.cancel()
                    if server_row and project_id:
                        watcher_task = asyncio.create_task(_watch_foreground_port_ready(
                            websocket=websocket,
                            server_row=server_row,
                            project_id=project_id,
                            port=port,
                        ))
            elif msg_type == 'resize':
                continue
            elif msg_type == 'close':
                break
    except WebSocketDisconnect:
        pass
    except WebSocketException:
        raise
    except Exception as exc:
        try:
            await websocket.send_json(_terminal_ws_response('error', {'message': f'\u7ec8\u7aef\u8fde\u63a5\u5f02\u5e38\uff1a{exc}'}))
        except Exception:
            pass
    finally:
        for task in [watcher_task, reader_task]:
            if task:
                task.cancel()
                try:
                    await task
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
        try:
            await websocket.close()
        except Exception:
            pass


@router.get('/servers', name='会话可用服务器', response_model=schemas.pspm.TerminalServerOptionsResponse)
async def list_terminal_servers(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
):
    """查询终端可连接服务器列表。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过项目管理菜单权限校验。

    返回：
    - TerminalServerOptionsResponse：可创建会话的服务器选项列表。
    """
    rows = await _list_allowed_servers(session, current_user)
    data = [
        schemas.pspm.TerminalServerOption(
            server_id=row.id,
            ip=row.ip,
            alias=row.alias,
            ssh_port=row.ssh_port,
        )
        for row in rows
    ]
    return schemas.pspm.TerminalServerOptionsResponse(data=data)


@router.post('/sessions/create', name='创建会话', response_model=schemas.pspm.TerminalSessionCreateResponse)
async def create_terminal_session(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    payload: schemas.pspm.TerminalSessionCreate,
):
    """创建一个新的终端会话。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户。
    - payload：服务器 IP 和会话别名。

    返回：
    - TerminalSessionCreateResponse：新会话 ID、提示符和欢迎语。
    """
    alias = (payload.alias or '').strip()
    if not alias:
        raise HTTPException(status_code=400, detail='会话别名不能为空')

    server = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)

    connect_code, connect_out, connect_err = await _run_server_shell(
        server,
        _wrap_remote_bash(f'test -d {shlex.quote(HOME_DIR)} && pwd'),
        timeout=15,
    )
    if connect_code != 0:
        msg = connect_err.strip() or connect_out.strip() or 'unknown error'
        raise HTTPException(status_code=400, detail=f'连接服务器失败：{msg}')

    session_id = uuid.uuid4().hex
    cwd = HOME_DIR
    host_label = (server.alias or server.ip or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL

    data = {
        'session_id': session_id,
        'user_id': current_user.id,
        'server_id': server.id,
        'server_ip': server.ip,
        'alias': alias,
        'cwd': cwd,
        'host_label': host_label,
        'conda_env_name': 'base',
    }
    await _set_session_data(session_id, data)

    return schemas.pspm.TerminalSessionCreateResponse(
        data=schemas.pspm.TerminalSessionInfo(
            session_id=session_id,
            server_ip=server.ip,
            alias=alias,
            cwd=cwd,
            prompt=_format_prompt_with_env(host_label, cwd, 'base'),
            welcome_message=f'连接成功：{server.ip}',
        )
    )


@router.post('/execute', name='执行命令', response_model=schemas.pspm.TerminalExecuteResponse)
async def execute_terminal_command(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    payload: schemas.pspm.TerminalExecuteRequest,
):
    """在指定终端会话中执行命令。

    参数：
    - session：数据库会话，此接口保留该参数以符合依赖签名。
    - current_user：当前登录用户。
    - payload：会话 ID 和用户输入命令。

    返回：
    - TerminalExecuteResponse：命令输出、错误输出、退出码和新提示符。
    """
    command = (payload.command or '').strip()
    if not command:
        raise HTTPException(status_code=400, detail='命令不能为空')

    session_data = await _get_session_data(payload.session_id, current_user.id)
    server_row = await _get_session_server_row(session, current_user, session_data)
    cwd = _normalize_cwd(session_data.get('cwd'))
    host_label = (session_data.get('host_label') or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL
    prompt_before = _format_prompt_with_env(host_label, cwd, session_data.get('conda_env_name'))

    tokens = _split_command(command)
    if not tokens:
        raise HTTPException(status_code=400, detail='命令不能为空')

    primary = tokens[0].lower()

    if primary == 'python' and len(tokens) == 1:
        msg = '交互模式不支持，请使用 python --version / python -c / python 脚本.py'
        return schemas.pspm.TerminalExecuteResponse(
            status='error',
            code=400,
            message=msg,
            data=schemas.pspm.TerminalExecuteResult(
                session_id=payload.session_id,
                command=command,
                cwd=cwd,
                prompt_before=prompt_before,
                prompt_after=prompt_before,
                exit_code=2,
                stdout='',
                stderr=msg,
                blocked=True,
                message=msg,
            ),
        )

    if primary == 'cd' and all(op not in command for op in ['&&', '||', ';', '|']):
        target = tokens[1] if len(tokens) > 1 else HOME_DIR
        next_cwd = _resolve_path(cwd, target)
        if not await _remote_path_is_dir(server_row, next_cwd):
            msg = f'bash: cd: {target}: No such file or directory'
            return schemas.pspm.TerminalExecuteResponse(
                status='error',
                code=400,
                message='目录不存在',
                data=schemas.pspm.TerminalExecuteResult(
                    session_id=payload.session_id,
                    command=command,
                    cwd=cwd,
                    prompt_before=prompt_before,
                    prompt_after=prompt_before,
                    exit_code=1,
                    stdout='',
                    stderr=msg,
                    blocked=False,
                    message='目录不存在',
                ),
            )

        updated = await _update_session_cwd(payload.session_id, current_user.id, next_cwd)
        next_prompt = _format_prompt_with_env(updated.get('host_label') or DEFAULT_HOST_LABEL, next_cwd, updated.get('conda_env_name'))
        return schemas.pspm.TerminalExecuteResponse(
            data=schemas.pspm.TerminalExecuteResult(
                session_id=payload.session_id,
                command=command,
                cwd=next_cwd,
                prompt_before=prompt_before,
                prompt_after=next_prompt,
                exit_code=0,
                stdout='',
                stderr='',
                blocked=False,
                message='ok',
            )
        )

    if primary == 'conda' and len(tokens) >= 3 and tokens[1].lower() == 'activate' and all(op not in command for op in ['&&', '||', ';', '|']):
        env_name = tokens[2].strip()
        check_code, check_out, check_err = await _run_terminal_command_on_server(
            server_row,
            f"conda env list | awk '{{print $1}}' | grep -Fx {shlex.quote(env_name)} >/dev/null",
            cwd,
            COMMAND_TIMEOUT_SECONDS,
            'base',
            False,
        )
        if check_code != 0:
            msg = check_err.strip() or check_out.strip() or f'Conda环境不存在：{env_name}'
            return schemas.pspm.TerminalExecuteResponse(
                status='error',
                code=400,
                message=msg,
                data=schemas.pspm.TerminalExecuteResult(
                    session_id=payload.session_id,
                    command=command,
                    cwd=cwd,
                    prompt_before=prompt_before,
                    prompt_after=prompt_before,
                    exit_code=1,
                    stdout='',
                    stderr=msg,
                    blocked=False,
                    message=msg,
                ),
            )
        async with _terminal_lock:
            data = _terminal_sessions.get(payload.session_id)
            if not data or data.get('user_id') != current_user.id:
                raise HTTPException(status_code=404, detail='会话不存在')
            data['conda_env_name'] = env_name
        prompt_after = _format_prompt_with_env(host_label, cwd, env_name)
        return schemas.pspm.TerminalExecuteResponse(
            data=schemas.pspm.TerminalExecuteResult(
                session_id=payload.session_id,
                command=command,
                cwd=cwd,
                prompt_before=prompt_before,
                prompt_after=prompt_after,
                exit_code=0,
                stdout='',
                stderr='',
                blocked=False,
                message='ok',
            )
        )

    if primary == 'conda' and len(tokens) >= 2 and tokens[1].lower() == 'deactivate' and all(op not in command for op in ['&&', '||', ';', '|']):
        async with _terminal_lock:
            data = _terminal_sessions.get(payload.session_id)
            if not data or data.get('user_id') != current_user.id:
                raise HTTPException(status_code=404, detail='会话不存在')
            data['conda_env_name'] = 'base'
        prompt_after = _format_prompt_with_env(host_label, cwd, 'base')
        return schemas.pspm.TerminalExecuteResponse(
            data=schemas.pspm.TerminalExecuteResult(
                session_id=payload.session_id,
                command=command,
                cwd=cwd,
                prompt_before=prompt_before,
                prompt_after=prompt_after,
                exit_code=0,
                stdout='',
                stderr='',
                blocked=False,
                message='ok',
            )
        )

    detach = str(getattr(payload, 'mode', '') or '').strip().lower() == 'foreground_start'
    exit_code, stdout, stderr = await _run_terminal_command_on_server(
        server_row,
        command,
        cwd,
        COMMAND_TIMEOUT_SECONDS,
        str(session_data.get('conda_env_name') or 'base'),
        detach,
    )

    prompt_after = _format_prompt_with_env(host_label, cwd, session_data.get('conda_env_name'))
    return schemas.pspm.TerminalExecuteResponse(
        data=schemas.pspm.TerminalExecuteResult(
            session_id=payload.session_id,
            command=command,
            cwd=cwd,
            prompt_before=prompt_before,
            prompt_after=prompt_after,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            blocked=False,
            message='ok' if exit_code == 0 else 'command failed',
        )
    )


@router.post('/complete', name='命令自动补全', response_model=schemas.pspm.TerminalCompleteResponse)
async def complete_terminal_command(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    payload: schemas.pspm.TerminalCompleteRequest,
):
    """根据当前命令内容返回 Tab 自动补全结果。

    参数：
    - session：数据库会话，此接口仅用于依赖注入占位。
    - current_user：当前登录用户。
    - payload：会话 ID 和当前命令内容。

    返回：
    - TerminalCompleteResponse：补全后的命令和候选项列表。
    """
    session_data = await _get_session_data(payload.session_id, current_user.id)
    server_row = await _get_session_server_row(session, current_user, session_data)
    cwd = _normalize_cwd(session_data.get('cwd'))
    original = str(payload.command or '')
    prefix, token = _extract_path_token(original)

    command_candidates: List[str] = []
    if _is_command_token(prefix, token):
        command_candidates = await _complete_command_candidates_on_server(server_row, token)

    candidates = command_candidates if command_candidates else await _complete_path_candidates_on_server(server_row, cwd, token)
    if not candidates:
        return schemas.pspm.TerminalCompleteResponse(
            data=schemas.pspm.TerminalCompleteResult(
                session_id=payload.session_id,
                original_command=original,
                completed_command=original,
                candidates=[],
                cwd=cwd,
                message='no_match',
            )
        )

    if len(candidates) == 1:
        completed = f'{prefix}{candidates[0]}'
        if command_candidates:
            completed = f'{completed} '
        elif not candidates[0].endswith('/'):
            completed = f'{completed} '
    else:
        cp = _common_prefix(candidates)
        completed = f'{prefix}{cp}' if cp and cp != token else original

    return schemas.pspm.TerminalCompleteResponse(
        data=schemas.pspm.TerminalCompleteResult(
            session_id=payload.session_id,
            original_command=original,
            completed_command=completed,
            candidates=candidates,
            cwd=cwd,
            message='ok',
        )
    )


@router.post('/sessions/close', name='关闭会话', response_model=schemas.base.BaseResponse)
async def close_terminal_session(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    payload: schemas.pspm.TerminalSessionClose,
):
    """关闭终端会话。

    参数：
    - session：数据库会话，此接口仅用于依赖注入占位。
    - current_user：当前登录用户。
    - payload：待关闭的会话 ID。

    返回：
    - BaseResponse：关闭成功提示。
    """
    _ = session
    removed = await _remove_session(payload.session_id, current_user.id)
    if not removed:
        raise HTTPException(status_code=404, detail='会话不存在')
    return schemas.base.BaseResponse(message='会话已关闭')
