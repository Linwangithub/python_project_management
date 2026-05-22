from __future__ import annotations

import asyncio
import base64
import json
import os
import posixpath
import pty
import re
import select as select_module
import secrets
import shlex
import signal
import subprocess
import tempfile
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.responses import Response, StreamingResponse
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
_terminal_download_tickets: Dict[str, Dict[str, Any]] = {}
_terminal_download_ticket_lock = asyncio.Lock()
DOWNLOAD_TICKET_TTL_SECONDS = 300

_ws_terminal_sessions: Dict[str, Dict[str, Any]] = {}
_ws_terminal_lock = asyncio.Lock()
WS_OUTPUT_BUFFER_LIMIT = 800

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


def _to_completion_display(cwd: str, token: str, abs_path: str, is_dir: bool) -> str:
    """Return a shell-like completion candidate relative to the token being completed."""
    normalized = os.path.normpath(abs_path)
    if token.startswith('/'):
        display = normalized
    elif token.startswith('~/'):
        home_path = f'{HOME_DIR}/'
        if normalized.startswith(home_path):
            display = f'~/{normalized[len(home_path):]}'
        else:
            display = normalized
    else:
        base_dir, _base_name = _resolve_completion_base(cwd, token)
        name = os.path.basename(normalized.rstrip('/'))
        token_dir = ''
        if '/' in token:
            token_dir = token.rsplit('/', 1)[0].rstrip('/')
        display = f'{token_dir}/{name}' if token_dir else name
    if is_dir and not display.endswith('/'):
        display = f'{display}/'
    return display

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
    """Return a shell-like completion candidate relative to the current input token."""
    base_dir, base_name = _resolve_completion_base(cwd, token)
    script = (
        f'base={shlex.quote(base_dir)}; '
        f'prefix={shlex.quote(base_name)}; '
        'if [ -d "$base" ]; then '
        'for item in "$base"/"$prefix"*; do '
        '  [ -e "$item" ] || continue; '
        '  [ -d "$item" ] && printf "d\t%s\n" "$item" || printf "f\t%s\n" "$item"; '
        'done | head -n 200; '
        'fi'
    )
    code, out, _err = await _run_server_shell(server_row, _wrap_remote_bash(script), timeout=3)
    if code != 0:
        return []

    candidates: List[str] = []
    for line in (out or '').splitlines():
        raw = (line or '').strip()
        if not raw or '\t' not in raw:
            continue
        kind, abs_path = raw.split('\t', 1)
        name = os.path.basename(abs_path.rstrip('/'))
        if not name.startswith(base_name):
            continue
        candidates.append(_to_completion_display(cwd, token, abs_path, kind == 'd'))
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


async def _get_transfer_session_context(
    session: SessionDep,
    current_user: schemas.users.Data,
    session_id: str,
) -> tuple[models.pspm.PspmServer, str]:
    """Resolve a legacy or WebSocket terminal session for file transfer."""
    try:
        session_data = await _get_session_data(session_id, current_user.id)
        server_row = await _get_session_server_row(session, current_user, session_data)
        return server_row, _normalize_cwd(session_data.get('cwd'))
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
    if not state or state.get('user_id') != current_user.id:
        raise HTTPException(status_code=404, detail='会话不存在')

    server_row = state.get('server_row')
    if not server_row:
        raise HTTPException(status_code=404, detail='会话不存在')
    cwd = str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR)
    return server_row, _normalize_cwd(cwd)


async def _get_terminal_session_context(
    session: SessionDep,
    current_user: schemas.users.Data,
    session_id: str,
) -> tuple[models.pspm.PspmServer, str, str]:
    """Resolve session context for command completion and file operations."""
    try:
        session_data = await _get_session_data(session_id, current_user.id)
        server_row = await _get_session_server_row(session, current_user, session_data)
        return server_row, _normalize_cwd(session_data.get('cwd')), str(session_data.get('conda_env_name') or 'base')
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
    if not state or state.get('user_id') != current_user.id:
        raise HTTPException(status_code=404, detail='会话不存在')
    server_row = state.get('server_row')
    if not server_row:
        raise HTTPException(status_code=404, detail='会话不存在')
    cwd = str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR)
    conda_env_name = str(state.get('conda_env_name') or state.get('foreground_conda_env_name') or 'base')
    return server_row, _normalize_cwd(cwd), conda_env_name


async def _build_terminal_completion_result(server_row, cwd: str, session_id: str, command: str) -> Dict[str, Any]:
    """Build the Tab completion result from the current terminal session context."""
    original = str(command or '')
    prefix, token = _extract_path_token(original)

    command_candidates: List[str] = []
    if _is_command_token(prefix, token):
        command_candidates = await _complete_command_candidates_on_server(server_row, token)

    candidates = command_candidates if command_candidates else await _complete_path_candidates_on_server(server_row, cwd, token)
    if not candidates:
        return {
            'session_id': session_id,
            'requested_command': original,
            'original_command': original,
            'completed_command': original,
            'candidates': [],
            'cwd': cwd,
            'message': 'no_match',
        }

    if len(candidates) == 1:
        completed = f'{prefix}{candidates[0]}'
        if command_candidates:
            completed = f'{completed} '
    else:
        cp = _common_prefix(candidates)
        completed = f'{prefix}{cp}' if cp and cp != token else original

    return {
        'session_id': session_id,
        'requested_command': original,
        'original_command': original,
        'completed_command': completed,
        'candidates': candidates,
        'cwd': cwd,
        'message': 'ok',
    }

def _get_transfer_root(current_user: schemas.users.Data) -> str:
    """Return the allowed root directory for the download browser."""
    username = str(getattr(current_user, 'username', '') or '').strip()
    is_root = int(getattr(current_user, 'id', 0) or 0) == 1 or username == 'root'
    if is_root:
        return HOME_DIR
    safe_username = re.sub(r'[^A-Za-z0-9._-]+', '', username)
    return f'/home/{safe_username or username or "user"}'


def _ensure_under_transfer_root(path: str, root: str) -> str:
    """Ensure a transfer path stays under the allowed root directory."""
    normalized = _normalize_cwd(path)
    normalized_root = _normalize_cwd(root)
    if normalized == normalized_root or normalized.startswith(f'{normalized_root}/'):
        return normalized
    raise HTTPException(status_code=400, detail='Path is outside the allowed download root')


def _resolve_transfer_browser_target(root: str, target_path: str | None) -> str:
    """Resolve the download browser target and clamp it under the allowed root."""
    raw = str(target_path or '').strip()
    if not raw:
        return _normalize_cwd(root)
    return _ensure_under_transfer_root(_resolve_path(_normalize_cwd(root), raw), root)

async def _find_ws_terminal_session_id_by_pid(user_id: int, pid: str) -> str:
    """Find a WebSocket terminal session by its PTY/SSH process PID."""
    target_pid = str(pid or '').strip()
    if not target_pid:
        return ''
    async with _ws_terminal_lock:
        for session_id, state in _ws_terminal_sessions.items():
            process = state.get('process')
            if state.get('user_id') == user_id and process and str(getattr(process, 'pid', '') or '') == target_pid:
                return session_id
    return ''




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


async def _mark_project_stopped(project_id: int) -> None:
    """Persist that a project is stopped."""
    if not project_id:
        return
    async with get_session() as db:
        await crud.projects.update_status(db, project_id=project_id, running=False)


async def _set_ws_foreground_state(session_id: str, project_id: int, pid: str, port: str) -> None:
    """Remember which project is bound to a foreground terminal session."""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        if not state:
            return
        state['foreground_project_id'] = int(project_id or 0)
        state['foreground_pid'] = str(pid or '').strip()
        state['foreground_port'] = str(port or '').strip()


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
    session_id: str,
    websocket: WebSocket,
    server_row: models.pspm.PspmServer,
    project_id: int,
    port: str,
    wait_seconds: int = 30,
) -> None:
    """Use a side shell to detect when the foreground service starts listening."""
    async with _ws_terminal_lock:
        state = _ws_terminal_sessions.get(session_id)
        session_cwd = str(state.get('foreground_cwd') or HOME_DIR) if state else HOME_DIR
        session_conda_env = str(state.get('foreground_conda_env_name') or 'base') if state else 'base'
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
        await _set_ws_foreground_state(session_id, project_id, pid, port)
        await _safe_send_json(websocket, _terminal_ws_response('foreground_started', {
            'project_id': project_id,
            'pid': pid,
            'port': port,
            'cwd': session_cwd,
            'conda_env_name': session_conda_env,
        }))
        return
    message = (err or out or '').strip() or '\u7b49\u5f85\u7aef\u53e3\u76d1\u542c\u8d85\u65f6\uff0c\u8bf7\u67e5\u770b\u7ec8\u7aef\u8f93\u51fa'
    await _safe_send_json(websocket, _terminal_ws_response('foreground_pending', {'message': message}))



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
        ok = await _safe_send_json(client, _terminal_ws_response('output', {'text': text}))
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
        async def _remove() -> None:
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
            await client.send_json(_terminal_ws_response('closed', {
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


@router.websocket('/ws', name='pspm_terminal_ws')
async def terminal_websocket(
    websocket: WebSocket,
    current_user: CurrentWSUser,
):
    """提供可重连的交互式 WebSocket 终端。

    设计说明：
    - 一个后端终端会话对应一个 PTY/SSH 进程，可被浏览器刷新后的新 WebSocket 重连。
    - 普通网络断开、页面刷新不会杀掉 PTY/SSH 进程，也不会取消前台启动端口检测任务。
    - 只有前端明确发送 `type=close` 或调用关闭接口时，才真正关闭终端会话。
    """
    await websocket.accept(subprotocol='pspm-terminal')
    session_id = ''
    explicit_close = False

    try:
        first = await websocket.receive_json()
        if first.get('type') != 'open':
            await websocket.send_json(_terminal_ws_response('error', {'message': '终端首包必须是 open'}))
            return

        server_ip = str(first.get('server_ip') or '').strip()
        alias = str(first.get('alias') or '').strip() or 'terminal'
        requested_session_id = str(first.get('session_id') or first.get('remote_session_id') or '').strip()
        if not server_ip:
            await websocket.send_json(_terminal_ws_response('error', {'message': '服务器IP不能为空'}))
            return

        state, reconnected = await _get_or_create_ws_terminal_session(current_user, server_ip, alias, requested_session_id)
        session_id = str(state.get('session_id') or '')
        await _attach_ws_terminal_client(session_id, websocket)
        process = state.get('process')
        await websocket.send_json(_terminal_ws_response('ready', {
            'session_id': session_id,
            'server_ip': state.get('server_ip') or server_ip,
            'alias': state.get('alias') or alias,
            'pid': getattr(process, 'pid', None),
            'reconnected': reconnected,
            'cwd': state.get('cwd') or HOME_DIR,
            'conda_env_name': state.get('conda_env_name') or 'base',
        }))

        while True:
            data = await websocket.receive_json()
            msg_type = str(data.get('type') or '').strip()

            async with _ws_terminal_lock:
                state = _ws_terminal_sessions.get(session_id)
            if not state:
                await websocket.send_json(_terminal_ws_response('closed', {'message': '终端会话已关闭'}))
                break

            master_fd = state.get('master_fd')
            server_row = state.get('server_row')

            if msg_type == 'input':
                text = str(data.get('text') or '')
                stripped_text = text.strip()
                if stripped_text.startswith('cd '):
                    async with _ws_terminal_lock:
                        active_state = _ws_terminal_sessions.get(session_id)
                        if active_state:
                            active_state['cwd'] = _resolve_path(str(active_state.get('cwd') or HOME_DIR), stripped_text[3:].strip())
                elif stripped_text.startswith('conda activate '):
                    async with _ws_terminal_lock:
                        active_state = _ws_terminal_sessions.get(session_id)
                        if active_state:
                            active_state['conda_env_name'] = stripped_text.split()[-1] if stripped_text.split() else 'base'
                elif stripped_text == 'conda deactivate':
                    async with _ws_terminal_lock:
                        active_state = _ws_terminal_sessions.get(session_id)
                        if active_state:
                            active_state['conda_env_name'] = 'base'
                if master_fd is not None:
                    _write_pty(master_fd, text)
            elif msg_type == 'run_foreground':
                project_id = int(data.get('project_id') or 0)
                port = str(data.get('port') or '').strip()
                work_dir = str(data.get('work_dir') or '').strip()
                conda_env_name = str(data.get('conda_env_name') or '').strip()
                command_text = str(data.get('command') or '').strip()
                if not command_text:
                    await websocket.send_json(_terminal_ws_response('error', {'message': '暂无配置启动命令'}))
                    continue
                if master_fd is not None:
                    if work_dir:
                        _write_pty(master_fd, f"cd {shlex.quote(work_dir)}\n")
                        await asyncio.sleep(0.2)
                    if conda_env_name:
                        _write_pty(master_fd, f"conda activate {shlex.quote(conda_env_name)}\n")
                        await asyncio.sleep(0.2)
                    _write_pty(master_fd, f"{command_text}\n")
                    async with _ws_terminal_lock:
                        active_state = _ws_terminal_sessions.get(session_id)
                        if active_state:
                            active_state['foreground_project_id'] = int(project_id or 0)
                            active_state['foreground_cwd'] = work_dir or HOME_DIR
                            active_state['foreground_conda_env_name'] = conda_env_name or 'base'
                            active_state['cwd'] = work_dir or HOME_DIR
                            active_state['conda_env_name'] = conda_env_name or 'base'
                    await websocket.send_json(_terminal_ws_response('foreground_pending', {
                        'message': '启动命令已进入前台运行，正在等待端口监听',
                        'cwd': work_dir or HOME_DIR,
                        'conda_env_name': conda_env_name or 'base',
                    }))
                    if server_row and project_id:
                        watcher_task = asyncio.create_task(_watch_foreground_port_ready(
                            session_id=session_id,
                            websocket=websocket,
                            server_row=server_row,
                            project_id=project_id,
                            port=port,
                        ))
                        await _track_ws_watcher_task(session_id, watcher_task)
            elif msg_type == 'complete':
                command_text = str(data.get('command') or '')
                cwd = _normalize_cwd(str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR))
                if server_row:
                    result = await _build_terminal_completion_result(server_row, cwd, session_id, command_text)
                    await websocket.send_json(_terminal_ws_response('complete_result', result))
            elif msg_type == 'resize':
                continue
            elif msg_type == 'close':
                explicit_close = True
                break
    except WebSocketDisconnect:
        pass
    except WebSocketException:
        raise
    except Exception as exc:
        try:
            await websocket.send_json(_terminal_ws_response('error', {'message': f'终端连接异常：{exc}'}))
        except Exception:
            pass
    finally:
        if session_id:
            await _detach_ws_terminal_client(session_id, websocket)
        if explicit_close and session_id:
            await _close_ws_terminal_session(session_id, current_user.id)
        try:
            await websocket.close()
        except Exception:
            pass




def _safe_transfer_name(name: str, fallback: str = 'pspm_upload') -> str:
    value = posixpath.basename(str(name or '').strip().replace('\\', '/'))
    value = re.sub(r'[^A-Za-z0-9._\-\u4e00-\u9fa5]+', '_', value).strip('._')
    return value or fallback


def _resolve_transfer_target(cwd: str, target_path: str | None) -> str:
    raw = str(target_path or '').strip()
    if not raw:
        raw = cwd or HOME_DIR
    return _resolve_path(_normalize_cwd(cwd), raw)


def _safe_transfer_relative_path(relative_path: str | None) -> str:
    value = str(relative_path or '').strip().replace('\\', '/')
    if not value:
        return ''
    value = posixpath.normpath(value)
    if value in {'.', '..'} or value.startswith('../') or value.startswith('/'):
        return ''
    parts = []
    for raw_part in value.split('/'):
        part = _safe_transfer_name(raw_part, '')
        if not part:
            continue
        parts.append(part)
    return '/'.join(parts)


async def _cleanup_expired_download_tickets() -> None:
    """清理过期的一次性下载票据。"""
    now = time.time()
    async with _terminal_download_ticket_lock:
        expired = [
            ticket
            for ticket, data in _terminal_download_tickets.items()
            if float(data.get('expires_at') or 0) <= now
        ]
        for ticket in expired:
            _terminal_download_tickets.pop(ticket, None)


async def _create_download_ticket(*, user_id: int, server_row, remote_path: str, filename: str, kind: str) -> str:
    """生成浏览器原生下载使用的一次性 ticket。"""
    await _cleanup_expired_download_tickets()
    ticket = secrets.token_urlsafe(32)
    async with _terminal_download_ticket_lock:
        _terminal_download_tickets[ticket] = {
            'user_id': int(user_id),
            'server_id': int(getattr(server_row, 'id', 0) or 0),
            'server_ip': str(getattr(server_row, 'ip', '') or ''),
            'ssh_port': int(getattr(server_row, 'ssh_port', 22) or 22),
            'root_password': str(getattr(server_row, 'root_password', '') or ''),
            'remote_path': remote_path,
            'filename': filename,
            'kind': kind,
            'expires_at': time.time() + DOWNLOAD_TICKET_TTL_SECONDS,
        }
    return ticket


async def _consume_download_ticket(ticket: str) -> Dict[str, Any]:
    """读取并立即删除一次性下载 ticket。"""
    await _cleanup_expired_download_tickets()
    safe_ticket = str(ticket or '').strip()
    if not safe_ticket:
        raise HTTPException(status_code=400, detail='下载凭证不能为空')
    async with _terminal_download_ticket_lock:
        data = _terminal_download_tickets.pop(safe_ticket, None)
    if not data:
        raise HTTPException(status_code=404, detail='下载凭证不存在或已过期')
    if float(data.get('expires_at') or 0) <= time.time():
        raise HTTPException(status_code=404, detail='下载凭证已过期')
    return data


async def _open_server_download_process(server_row, remote_path: str, kind: str) -> subprocess.Popen:
    """打开一个流式下载进程，让浏览器可以直接显示下载进度。"""
    safe_path = shlex.quote(remote_path)
    if kind == 'dir':
        parent = posixpath.dirname(remote_path.rstrip('/')) or '/'
        child = posixpath.basename(remote_path.rstrip('/'))
        remote_command = (
            f'cd {shlex.quote(parent)} && '
            f'if command -v zip >/dev/null 2>&1; then '
            f'zip -r -q - {shlex.quote(child)}; '
            f'else '
            f'python3 -c {shlex.quote("import os,sys,zipfile\\nbase=sys.argv[1]\\nout=zipfile.ZipFile(sys.stdout.buffer, \\'w\\', zipfile.ZIP_DEFLATED)\\nfor root, dirs, files in os.walk(base):\\n    dirs[:] = [d for d in dirs if d not in {\\'.git\\', \\'__pycache__\\'}]\\n    for name in files:\\n        path=os.path.join(root,name)\\n        out.write(path, os.path.relpath(path, os.path.dirname(base)))\\nout.close()")} {shlex.quote(child)}; '
            f'fi'
        )
    else:
        remote_command = f'cat {safe_path}'

    ip = str(getattr(server_row, 'ip', '') or '').strip()
    if await _is_local_server_ip_async(ip):
        return await asyncio.create_subprocess_exec(
            '/bin/bash',
            '-lc',
            remote_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    if not re.match(r'^[A-Za-z0-9_.:-]+$', ip):
        raise HTTPException(status_code=400, detail=f'服务器IP格式不合法：{ip}')

    password = str(getattr(server_row, 'root_password', '') or '')
    ssh_port = int(getattr(server_row, 'ssh_port', 22) or 22)
    ssh_opts = f'-p {ssh_port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR'
    remote = f'root@{shlex.quote(ip)}'
    quoted_command = shlex.quote(remote_command)
    if password and (await _run_shell('command -v sshpass >/dev/null 2>&1', timeout=5))[0] == 0:
        shell_cmd = f'sshpass -p {shlex.quote(password)} ssh {ssh_opts} {remote} {quoted_command}'
    elif password and (await _run_shell('command -v setsid >/dev/null 2>&1', timeout=5))[0] == 0:
        shell_cmd = _build_askpass_ssh_command(password, f'ssh {ssh_opts} {remote} {quoted_command}')
    elif password:
        raise HTTPException(status_code=500, detail='当前后端未安装 sshpass/setsid，无法创建远程下载通道')
    else:
        shell_cmd = f'ssh {ssh_opts} -o BatchMode=yes {remote} {quoted_command}'

    return await asyncio.create_subprocess_exec(
        '/bin/bash',
        '-lc',
        shell_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _stream_server_download(server_row, remote_path: str, kind: str):
    """把远端文件或目录流式转发给浏览器。"""
    process = await _open_server_download_process(server_row, remote_path, kind)
    try:
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(1024 * 256)
            if not chunk:
                break
            yield chunk
        await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def _upload_terminal_file_to_server(server_row, local_path: str, remote_path: str) -> None:
    parent = posixpath.dirname(remote_path) or HOME_DIR
    with open(local_path, 'rb') as fh:
        encoded = base64.b64encode(fh.read()).decode('ascii')
    script = (
        f'mkdir -p {shlex.quote(parent)} && '
        f'base64 -d > {shlex.quote(remote_path)} <<\'PSPM_UPLOAD_EOF\'\n'
        f'{encoded}\n'
        'PSPM_UPLOAD_EOF\n'
    )
    code, out, err = await _run_server_shell(server_row, script, timeout=1800)
    if code != 0:
        raise HTTPException(status_code=400, detail=(err.strip() or out.strip() or '上传失败'))


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


@router.post('/upload', name='终端上传', response_model=schemas.base.BaseResponse)
async def upload_terminal_file(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str = Form(...),
    target_path: str = Form(''),
    relative_path: str = Form(''),
    file: UploadFile = File(...),
):
    """把文件上传到当前终端会话所在服务器。"""
    server_row, cwd = await _get_transfer_session_context(session, current_user, session_id)
    target = _resolve_transfer_target(cwd, target_path)
    safe_name = _safe_transfer_name(file.filename or 'upload.bin', 'upload.bin')
    safe_relative_path = _safe_transfer_relative_path(relative_path)
    if safe_relative_path:
        remote_path = posixpath.join(target, safe_relative_path)
    else:
        code, _out, _err = await _run_server_shell(server_row, f'test -d {shlex.quote(target)}', timeout=10)
        remote_path = posixpath.join(target, safe_name) if code == 0 else target
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.flush()
    try:
        await _upload_terminal_file_to_server(server_row, tmp_path, remote_path)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    return schemas.base.BaseResponse(message=f'上传完成：{remote_path}')


@router.post('/download-ticket', name='终端创建下载凭证', response_model=schemas.base.ItemResponse)
async def create_terminal_download_ticket(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str,
    path: str,
):
    """创建一次性下载凭证，供浏览器原生下载使用。"""
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    remote_path = _resolve_transfer_browser_target(root, path)
    base_name = _safe_transfer_name(posixpath.basename(remote_path.rstrip('/')) or 'download', 'download')
    quoted_path = shlex.quote(remote_path)
    check_script = (
        f'if [ -d {quoted_path} ]; then echo "dir\t0"; '
        f'elif [ -f {quoted_path} ]; then printf "file\\t%s\\n" "$(stat -c %s {quoted_path} 2>/dev/null || echo 0)"; '
        'else echo "missing\t0"; fi'
    )
    code, out, _err = await _run_server_shell(server_row, check_script, timeout=10)
    last_line = (out or '').strip().splitlines()[-1] if code == 0 and (out or '').strip() else 'missing\t0'
    kind, _, size_text = last_line.partition('\t')
    kind = kind.strip()
    if kind == 'missing':
        raise HTTPException(status_code=404, detail='文件或目录不存在')
    filename = f'{base_name}.zip' if kind == 'dir' else base_name
    ticket = await _create_download_ticket(
        user_id=current_user.id,
        server_row=server_row,
        remote_path=remote_path,
        filename=filename,
        kind=kind,
    )
    async with _terminal_download_ticket_lock:
        if ticket in _terminal_download_tickets:
            _terminal_download_tickets[ticket]['size'] = int(size_text or 0) if str(size_text or '').isdigit() else 0
    return schemas.base.ItemResponse(data={
        'ticket': ticket,
        'filename': filename,
        'size': int(size_text or 0) if str(size_text or '').isdigit() else 0,
        'expires_in': DOWNLOAD_TICKET_TTL_SECONDS,
    })


@router.get('/download-direct', name='终端原生下载')
async def download_terminal_file_direct(ticket: str):
    """使用一次性凭证流式下载文件或目录，让浏览器原生下载栏显示进度。"""
    data = await _consume_download_ticket(ticket)
    server_row = SimpleNamespace(
        id=data.get('server_id'),
        ip=data.get('server_ip'),
        ssh_port=data.get('ssh_port') or 22,
        root_password=data.get('root_password') or '',
    )
    remote_path = str(data.get('remote_path') or '')
    kind = str(data.get('kind') or 'file')
    filename = str(data.get('filename') or 'download')
    media_type = 'application/zip' if kind == 'dir' else 'application/octet-stream'
    header_name = quote(filename)
    headers = {'Content-Disposition': f"attachment; filename*=UTF-8''{header_name}"}
    size = int(data.get('size') or 0)
    if kind == 'file' and size > 0:
        headers['Content-Length'] = str(size)
    return StreamingResponse(
        _stream_server_download(server_row, remote_path, kind),
        media_type=media_type,
        headers=headers,
    )


@router.get('/download', name='终端下载')
async def download_terminal_file(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str,
    path: str,
):
    """从当前终端会话所在服务器下载文件或目录。"""
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    remote_path = _resolve_transfer_browser_target(root, path)
    base_name = _safe_transfer_name(posixpath.basename(remote_path.rstrip('/')) or 'download', 'download')
    quoted_path = shlex.quote(remote_path)
    check_script = f'if [ -d {quoted_path} ]; then echo dir; elif [ -f {quoted_path} ]; then echo file; else echo missing; fi'
    code, out, _err = await _run_server_shell(server_row, check_script, timeout=10)
    kind = (out or '').strip().splitlines()[-1] if code == 0 and (out or '').strip() else 'missing'
    if kind == 'missing':
        raise HTTPException(status_code=404, detail='文件或目录不存在')
    if kind == 'dir':
        filename = f'{base_name}.zip'
        parent = posixpath.dirname(remote_path.rstrip('/')) or '/'
        child = posixpath.basename(remote_path.rstrip('/'))
        zip_script = (
            f'cd {shlex.quote(parent)} && '
            f'if command -v zip >/dev/null 2>&1; then '
            f'zip -r -q - {shlex.quote(child)}; '
            f'else '
            f'python3 -c {shlex.quote("import os,sys,zipfile\\nbase=sys.argv[1]\\nout=zipfile.ZipFile(sys.stdout.buffer, \\'w\\', zipfile.ZIP_DEFLATED)\\nfor root, dirs, files in os.walk(base):\\n    dirs[:] = [d for d in dirs if d not in {\\'.git\\', \\'__pycache__\\'}]\\n    for name in files:\\n        path=os.path.join(root,name)\\n        out.write(path, os.path.relpath(path, os.path.dirname(base)))\\nout.close()")} {shlex.quote(child)}; '
            f'fi'
        )
        cmd = f'{zip_script} | base64 -w 0'
        media_type = 'application/zip'
    else:
        filename = base_name
        cmd = f'base64 -w 0 {quoted_path}'
        media_type = 'application/octet-stream'
    code, out, err = await _run_server_shell(server_row, cmd, timeout=1800)
    if code != 0:
        raise HTTPException(status_code=400, detail=(err.strip() or out.strip() or '下载失败'))
    try:
        data = base64.b64decode((out or '').strip())
    except Exception:
        raise HTTPException(status_code=500, detail='下载内容解析失败')
    header_name = quote(filename)
    return Response(
        content=data,
        media_type=media_type,
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{header_name}"},
    )


@router.get('/list-path', name='终端文件列表', response_model=schemas.base.ItemResponse)
async def list_terminal_path(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str,
    path: str = '',
):
    """列出当前终端会话所在服务器的文件和目录，供下载弹框下拉选择。"""
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    target = _resolve_transfer_browser_target(root, path)
    quoted_target = shlex.quote(target)
    script = (
        f'target={quoted_target}; '
        'if [ -d "$target" ]; then '
        'find "$target" -maxdepth 1 -mindepth 1 -printf "%y\\t%p\\n" 2>/dev/null | sort; '
        'elif [ -f "$target" ]; then '
        'printf "f\\t%s\\n" "$target"; '
        'else exit 44; fi'
    )
    code, out, err = await _run_server_shell(server_row, script, timeout=20)
    if code != 0:
        raise HTTPException(status_code=404, detail=(err.strip() or out.strip() or '文件或目录不存在'))
    items = []
    for line in (out or '').splitlines():
        if '\t' not in line:
            continue
        kind, item_path = line.split('\t', 1)
        name = posixpath.basename(item_path.rstrip('/')) or item_path
        if name.startswith('.'):
            continue
        items.append({
            'name': name,
            'path': item_path,
            'type': 'dir' if kind == 'd' else 'file',
        })
    root = _normalize_cwd(root)
    if target == root:
        parent = root
        can_go_parent = False
    else:
        parent = posixpath.dirname(target.rstrip('/')) or root
        if parent == '.':
            parent = root
        parent = _ensure_under_transfer_root(parent, root)
        can_go_parent = True
    return schemas.base.ItemResponse(data={
        'cwd': target,
        'root': root,
        'parent': parent,
        'can_go_parent': can_go_parent,
        'items': items,
    })


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
    server_row, cwd, _conda_env_name = await _get_terminal_session_context(session, current_user, payload.session_id)
    result = await _build_terminal_completion_result(
        server_row=server_row,
        cwd=cwd,
        session_id=payload.session_id,
        command=str(payload.command or ''),
    )
    return schemas.pspm.TerminalCompleteResponse(
        data=schemas.pspm.TerminalCompleteResult(**result)
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
    removed_legacy = await _remove_session(payload.session_id, current_user.id)
    ws_session_id = payload.session_id
    async with _ws_terminal_lock:
        has_ws_session = ws_session_id in _ws_terminal_sessions
    if not has_ws_session:
        ws_session_id = await _find_ws_terminal_session_id_by_pid(current_user.id, payload.session_id)
    removed_ws = await _close_ws_terminal_session(ws_session_id, current_user.id) if ws_session_id else False
    if not removed_legacy and not removed_ws:
        raise HTTPException(status_code=404, detail='会话不存在')
    return schemas.base.BaseResponse(message='会话已关闭')
