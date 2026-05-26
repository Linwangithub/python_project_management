"""终端会话上下文解析服务。

本模块兼容传统 HTTP 终端会话和 WebSocket 终端会话，统一解析文件传输、
命令补全等接口需要的服务器、当前目录和 Conda 环境信息。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app import models, schemas
from app.core.deps import SessionDep
from app.services.pspm.terminal_access import _get_session_server_row
from app.services.pspm.terminal_legacy_session import _get_session_data
from app.services.pspm.terminal_ws_session import _ws_terminal_lock, _ws_terminal_sessions
from app.utils.pspm.project_config import TERMINAL_HOME_DIR
from app.utils.pspm.terminal_config import terminal_message
from app.utils.pspm.terminal_paths import _normalize_cwd

# WebSocket 会话字典类型别名，便于说明调用方传入的是共享状态容器。
WsSessionStore = Dict[str, Dict[str, Any]]

# 终端默认 home 目录，用于 WebSocket 会话缺省 cwd 兜底。
HOME_DIR = TERMINAL_HOME_DIR


async def _get_transfer_session_context(
    session: SessionDep,
    current_user: schemas.users.Data,
    session_id: str,
) -> tuple[models.pspm.PspmServer, str]:
    """解析文件传输接口可使用的传统会话或 WebSocket 会话上下文。"""
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
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))

    server_row = state.get('server_row')
    if not server_row:
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
    cwd = str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR)
    return server_row, _normalize_cwd(cwd)

async def _get_terminal_session_context(
    session: SessionDep,
    current_user: schemas.users.Data,
    session_id: str,
) -> tuple[models.pspm.PspmServer, str, str]:
    """解析命令补全和文件操作需要的终端会话上下文。"""
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
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
    server_row = state.get('server_row')
    if not server_row:
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
    cwd = str(state.get('cwd') or state.get('foreground_cwd') or HOME_DIR)
    conda_env_name = str(state.get('conda_env_name') or state.get('foreground_conda_env_name') or 'base')
    return server_row, _normalize_cwd(cwd), conda_env_name
