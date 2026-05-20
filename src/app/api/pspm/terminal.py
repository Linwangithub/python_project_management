from __future__ import annotations

import asyncio
import os
import shlex
import uuid
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app import crud, models, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

HOME_DIR = '/root'
DEFAULT_HOST_LABEL = 'wcp'
COMMAND_TIMEOUT_SECONDS = 30
CONDA_INIT = 'source /root/miniforge3/etc/profile.d/conda.sh >/dev/null 2>&1 || true; '

_terminal_sessions: Dict[str, Dict[str, Any]] = {}
_terminal_lock = asyncio.Lock()


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

    session_id = uuid.uuid4().hex
    cwd = HOME_DIR
    host_label = (server.alias or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL

    data = {
        'session_id': session_id,
        'user_id': current_user.id,
        'server_id': server.id,
        'server_ip': server.ip,
        'alias': alias,
        'cwd': cwd,
        'host_label': host_label,
    }
    await _set_session_data(session_id, data)

    return schemas.pspm.TerminalSessionCreateResponse(
        data=schemas.pspm.TerminalSessionInfo(
            session_id=session_id,
            server_ip=server.ip,
            alias=alias,
            cwd=cwd,
            prompt=_format_prompt(host_label, cwd),
            welcome_message='连接成功！',
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
    cwd = _normalize_cwd(session_data.get('cwd'))
    host_label = (session_data.get('host_label') or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL
    prompt_before = _format_prompt(host_label, cwd)

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
        if not os.path.isdir(next_cwd):
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
        next_prompt = _format_prompt(updated.get('host_label') or DEFAULT_HOST_LABEL, next_cwd)
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

    process = await asyncio.create_subprocess_shell(
        f'{CONDA_INIT}{command}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        executable='/bin/bash',
    )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    stdout = (stdout_bytes or b'').decode('utf-8', errors='replace')
    stderr = (stderr_bytes or b'').decode('utf-8', errors='replace')
    exit_code = 124 if timed_out else int(process.returncode or 0)

    if timed_out:
        timeout_msg = f'命令执行超时（>{COMMAND_TIMEOUT_SECONDS}s）'
        stderr = f'{stderr}\n{timeout_msg}'.strip()

    prompt_after = _format_prompt(host_label, cwd)
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
    _ = session
    session_data = await _get_session_data(payload.session_id, current_user.id)
    cwd = _normalize_cwd(session_data.get('cwd'))
    original = str(payload.command or '')
    prefix, token = _extract_path_token(original)

    command_candidates: List[str] = []
    if _is_command_token(prefix, token):
        command_candidates = await _complete_command_candidates(token)

    candidates = command_candidates if command_candidates else _complete_path_candidates(cwd, token)
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
