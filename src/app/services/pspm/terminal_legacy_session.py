"""传统 HTTP 终端会话内存存储服务。

本模块维护非 WebSocket 终端接口仍在使用的内存会话字典和锁。
后续接口层只通过这里读写会话，避免全局状态散落在路由文件中。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import HTTPException

from app.utils.pspm.terminal_config import terminal_message

# 传统 HTTP 终端会话存储，key 为 session_id，value 为会话上下文。
terminal_sessions: Dict[str, Dict[str, Any]] = {}

# 保护传统会话字典读写的异步锁。
terminal_lock = asyncio.Lock()


async def _set_session_data(session_id: str, data: Dict[str, Any]) -> None:
    """写入内存终端会话数据。

    参数：
    - session_id：终端会话唯一 ID。
    - data：需要保存的会话上下文。

    返回：
    - None。
    """
    async with terminal_lock:
        terminal_sessions[session_id] = data

async def _get_session_data(session_id: str, user_id: int) -> Dict[str, Any]:
    """读取并校验当前用户的终端会话数据。

    参数：
    - session_id：终端会话唯一 ID。
    - user_id：当前登录用户 ID。

    返回：
    - Dict[str, Any]：会话上下文数据。
    """
    async with terminal_lock:
        data = terminal_sessions.get(session_id)

    if not data or data.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
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
    async with terminal_lock:
        data = terminal_sessions.get(session_id)
        if not data or data.get('user_id') != user_id:
            raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
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
    async with terminal_lock:
        data = terminal_sessions.get(session_id)
        if not data or data.get('user_id') != user_id:
            return False
        terminal_sessions.pop(session_id, None)
        return True
