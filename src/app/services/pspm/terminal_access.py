"""终端服务器访问权限查询服务。

本模块集中维护终端可用服务器列表、按 IP/ID 校验服务器权限、以及从会话上下文解析服务器记录的逻辑。
接口层只调用这些服务，不直接拼 SQL 或重复用户权限判断。
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy import select

from app import crud, models, schemas
from app.core.deps import SessionDep
from app.utils.pspm.terminal_config import terminal_message


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
        raise HTTPException(status_code=400, detail=terminal_message('server_ip_required'))

    candidates = await _list_allowed_servers(session, current_user)
    for server in candidates:
        if server.ip == ip:
            return server

    raise HTTPException(status_code=403, detail=terminal_message('server_forbidden'))

async def _get_allowed_server_by_id(
    session: SessionDep,
    current_user: schemas.users.Data,
    server_id: int | None,
) -> models.pspm.PspmServer:
    """按服务器 ID 查找当前用户可使用的服务器。"""
    if not server_id:
        raise HTTPException(status_code=400, detail=terminal_message('session_server_id_missing'))

    candidates = await _list_allowed_servers(session, current_user)
    for server in candidates:
        if int(server.id) == int(server_id):
            return server

    raise HTTPException(status_code=403, detail=terminal_message('server_forbidden'))

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
