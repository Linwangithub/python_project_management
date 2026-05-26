"""终端接口模块，提供独立终端会话、WebSocket 交互、上传下载和命令补全能力。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

import asyncio
import shlex
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, WebSocketException

from app import schemas
from app.api.deps import CurrentWSUser, require_permission
from app.core.deps import SessionDep
from app.services.pspm.terminal_context import _get_terminal_session_context
from app.services.pspm.terminal_completion import _build_terminal_completion_result
from app.services.pspm.terminal_ws_session import (
    _attach_ws_terminal_client,
    _close_ws_terminal_session,
    _detach_ws_terminal_client,
    _find_ws_terminal_session_id_by_pid,
    _get_or_create_ws_terminal_session,
    _track_ws_watcher_task,
    _watch_foreground_port_ready,
    _ws_terminal_lock,
    _ws_terminal_sessions,
)
from app.services.pspm.terminal_execute_service import execute_terminal_command_service
from app.services.pspm.terminal_transfer_service import (
    create_terminal_download_ticket_service,
    download_terminal_file_direct_service,
    download_terminal_file_legacy_service,
    list_terminal_path_service,
    upload_terminal_file_service,
)
from app.services.pspm.terminal_legacy_session import (
    _remove_session,
    _set_session_data,
)
from app.services.pspm.terminal_access import (
    _get_allowed_server_by_ip,
    _list_allowed_servers,
)
from app.utils.pspm.project_config import (
    TERMINAL_COMMAND_TIMEOUT_SECONDS,
    TERMINAL_DEFAULT_HOST_LABEL,
    TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS,
    TERMINAL_HOME_DIR,
)
from app.utils.pspm.shell_utils import _run_server_shell
from app.utils.pspm.terminal_config import (
    TERMINAL_DEFAULT_ALIAS,
    TERMINAL_DEFAULT_CONDA_ENV,
    WS_RESPONSE_CLOSED,
    WS_RESPONSE_COMPLETE_RESULT,
    WS_RESPONSE_ERROR,
    WS_RESPONSE_FOREGROUND_PENDING,
    WS_RESPONSE_READY,
    WS_TYPE_CLOSE,
    WS_TYPE_COMPLETE,
    WS_TYPE_INPUT,
    WS_TYPE_OPEN,
    WS_TYPE_RESIZE,
    WS_TYPE_RUN_FOREGROUND,
    terminal_message,
)

from app.utils.pspm.terminal_ws_helpers import (
    _terminal_ws_response,
    _write_pty,
)
from app.utils.pspm.terminal_shell import _wrap_remote_bash
from app.utils.pspm.terminal_paths import (
    _format_prompt_with_env,
    _normalize_cwd,
    _resolve_path,
)

router = APIRouter()

HOME_DIR = TERMINAL_HOME_DIR
DEFAULT_HOST_LABEL = TERMINAL_DEFAULT_HOST_LABEL
COMMAND_TIMEOUT_SECONDS = TERMINAL_COMMAND_TIMEOUT_SECONDS

DOWNLOAD_TICKET_TTL_SECONDS = TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS



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
        if first.get('type') != WS_TYPE_OPEN:
            await websocket.send_json(_terminal_ws_response(WS_RESPONSE_ERROR, {'message': terminal_message('ws_first_packet_required')}))
            return

        server_ip = str(first.get('server_ip') or '').strip()
        alias = str(first.get('alias') or '').strip() or TERMINAL_DEFAULT_ALIAS
        requested_session_id = str(first.get('session_id') or first.get('remote_session_id') or '').strip()
        if not server_ip:
            await websocket.send_json(_terminal_ws_response(WS_RESPONSE_ERROR, {'message': terminal_message('server_ip_required')}))
            return

        state, reconnected = await _get_or_create_ws_terminal_session(current_user, server_ip, alias, requested_session_id)
        session_id = str(state.get('session_id') or '')
        await _attach_ws_terminal_client(session_id, websocket)
        process = state.get('process')
        await websocket.send_json(_terminal_ws_response(WS_RESPONSE_READY, {
            'session_id': session_id,
            'server_ip': state.get('server_ip') or server_ip,
            'alias': state.get('alias') or alias,
            'pid': getattr(process, 'pid', None),
            'reconnected': reconnected,
            'cwd': state.get('cwd') or HOME_DIR,
            'conda_env_name': state.get('conda_env_name') or TERMINAL_DEFAULT_CONDA_ENV,
        }))

        while True:
            data = await websocket.receive_json()
            msg_type = str(data.get('type') or '').strip()

            async with _ws_terminal_lock:
                state = _ws_terminal_sessions.get(session_id)
            if not state:
                await websocket.send_json(_terminal_ws_response(WS_RESPONSE_CLOSED, {'message': terminal_message('terminal_session_closed')}))
                break

            master_fd = state.get('master_fd')
            server_row = state.get('server_row')

            if msg_type == WS_TYPE_INPUT:
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
                            active_state['conda_env_name'] = stripped_text.split()[-1] if stripped_text.split() else TERMINAL_DEFAULT_CONDA_ENV
                elif stripped_text == 'conda deactivate':
                    async with _ws_terminal_lock:
                        active_state = _ws_terminal_sessions.get(session_id)
                        if active_state:
                            active_state['conda_env_name'] = TERMINAL_DEFAULT_CONDA_ENV
                if master_fd is not None:
                    _write_pty(master_fd, text)
            elif msg_type == WS_TYPE_RUN_FOREGROUND:
                project_id = int(data.get('project_id') or 0)
                port = str(data.get('port') or '').strip()
                work_dir = str(data.get('work_dir') or '').strip()
                conda_env_name = str(data.get('conda_env_name') or '').strip()
                command_text = str(data.get('command') or '').strip()
                if not command_text:
                    await websocket.send_json(_terminal_ws_response(WS_RESPONSE_ERROR, {'message': terminal_message('start_command_missing')}))
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
                            active_state['foreground_conda_env_name'] = conda_env_name or TERMINAL_DEFAULT_CONDA_ENV
                            active_state['cwd'] = work_dir or HOME_DIR
                            active_state['conda_env_name'] = conda_env_name or TERMINAL_DEFAULT_CONDA_ENV
                    await websocket.send_json(_terminal_ws_response(WS_RESPONSE_FOREGROUND_PENDING, {
                        'message': terminal_message('foreground_waiting_port'),
                        'cwd': work_dir or HOME_DIR,
                        'conda_env_name': conda_env_name or TERMINAL_DEFAULT_CONDA_ENV,
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
            elif msg_type == WS_TYPE_COMPLETE:
                command_text = str(data.get('command') or '')
                cwd = _normalize_cwd(str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR))
                if server_row:
                    result = await _build_terminal_completion_result(server_row, cwd, session_id, command_text)
                    await websocket.send_json(_terminal_ws_response(WS_RESPONSE_COMPLETE_RESULT, result))
            elif msg_type == WS_TYPE_RESIZE:
                continue
            elif msg_type == WS_TYPE_CLOSE:
                explicit_close = True
                break
    except WebSocketDisconnect:
        pass
    except WebSocketException:
        raise
    except Exception as exc:
        try:
            await websocket.send_json(_terminal_ws_response(WS_RESPONSE_ERROR, {'message': terminal_message('terminal_connection_exception', message=str(exc))}))
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
        raise HTTPException(status_code=400, detail=terminal_message('alias_required'))

    server = await _get_allowed_server_by_ip(session, current_user, payload.server_ip)

    connect_code, connect_out, connect_err = await _run_server_shell(
        server,
        _wrap_remote_bash(f'test -d {shlex.quote(HOME_DIR)} && pwd'),
        timeout=15,
    )
    if connect_code != 0:
        msg = connect_err.strip() or connect_out.strip() or terminal_message('unknown_error')
        raise HTTPException(status_code=400, detail=terminal_message('server_connect_failed', message=msg))

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
            welcome_message=terminal_message('server_connected', server_ip=server.ip),
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
    remote_path = await upload_terminal_file_service(
        session=session,
        current_user=current_user,
        session_id=session_id,
        target_path=target_path,
        relative_path=relative_path,
        file=file,
        ws_terminal_lock=_ws_terminal_lock,
        ws_terminal_sessions=_ws_terminal_sessions,
    )
    return schemas.base.BaseResponse(message=terminal_message('upload_completed', path=remote_path))

@router.post('/download-ticket', name='终端创建下载凭证', response_model=schemas.base.ItemResponse)
async def create_terminal_download_ticket(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str,
    path: str,
):
    """创建一次性下载凭证，供浏览器原生下载使用。"""
    data = await create_terminal_download_ticket_service(
        session=session,
        current_user=current_user,
        session_id=session_id,
        path=path,
        ws_terminal_lock=_ws_terminal_lock,
        ws_terminal_sessions=_ws_terminal_sessions,
    )
    return schemas.base.ItemResponse(data=data)

@router.get('/download-direct', name='终端原生下载')
async def download_terminal_file_direct(ticket: str):
    """使用一次性凭证流式下载文件或目录，让浏览器原生下载栏显示进度。"""
    return await download_terminal_file_direct_service(ticket)

@router.get('/download', name='终端下载')
async def download_terminal_file(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('project_management', None)),
    session_id: str,
    path: str,
):
    """从当前终端会话所在服务器下载文件或目录。"""
    return await download_terminal_file_legacy_service(
        session=session,
        current_user=current_user,
        session_id=session_id,
        path=path,
        ws_terminal_lock=_ws_terminal_lock,
        ws_terminal_sessions=_ws_terminal_sessions,
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
    data = await list_terminal_path_service(
        session=session,
        current_user=current_user,
        session_id=session_id,
        path=path,
        ws_terminal_lock=_ws_terminal_lock,
        ws_terminal_sessions=_ws_terminal_sessions,
    )
    return schemas.base.ItemResponse(data=data)

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
    return await execute_terminal_command_service(session, current_user, payload)

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
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
    return schemas.base.BaseResponse(message=terminal_message('session_closed'))
