"""项目管理 CRUD 模块，封装项目、服务器、环境和日志的数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

import re
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.crud.rbac import ROOT_ROLE_KEY, rbac

def role_keys_to_name(role_keys: List[str]) -> str:
    """把角色 key 列表转换为前端展示角色名。

    参数：
    - role_keys：当前用户绑定的角色 key 列表。

    返回：
    - str：`root` 或 `user`。
    """
    return 'root' if ROOT_ROLE_KEY in role_keys else 'user'


def project_status_to_name(status: int | None) -> str:
    """把项目运行状态数值转换为中文展示文案。

    参数：
    - status：项目表中的状态值，1 表示运行中，其它值表示已停止。

    返回：
    - str：运行中或已停止。
    """
    return '运行中' if status == 1 else '已停止'


def normalize_assigned_users(value: str | None) -> str:
    """规范化服务器已分配用户字符串。

    参数：
    - value：数据库中保存的逗号分隔用户名字符串。

    返回：
    - str：包含 root 且去重后的逗号分隔字符串。
    """
    raw = (value or '').strip()
    if not raw:
        return 'root'

    cleaned = raw.replace('，', ',').replace(' ', '')
    parts = [x for x in cleaned.split(',') if x]
    uniq: List[str] = []
    for p in parts:
        if p not in uniq:
            uniq.append(p)
    if 'root' not in uniq:
        uniq.insert(0, 'root')
    return ','.join(uniq)


def add_assigned_user(value: str | None, username: str) -> str:
    """把用户名追加到服务器已分配用户列表。

    参数：
    - value：原已分配用户字符串。
    - username：需要追加的用户名。

    返回：
    - str：追加并规范化后的已分配用户字符串。
    """
    current = normalize_assigned_users(value)
    parts = [x for x in current.split(',') if x]
    if username not in parts:
        parts.append(username)
    return normalize_assigned_users(','.join(parts))


def remove_assigned_user(value: str | None, username: str) -> str:
    """从服务器已分配用户列表中移除用户名。

    参数：
    - value：原已分配用户字符串。
    - username：需要移除的用户名。

    返回：
    - str：移除并规范化后的已分配用户字符串。
    """
    current = normalize_assigned_users(value)
    parts = [x for x in current.split(',') if x and x != username]
    return normalize_assigned_users(','.join(parts))


def is_valid_linux_username(username: str) -> bool:
    """校验 Linux 普通用户名格式。

    参数：
    - username：待校验用户名。

    返回：
    - bool：是否满足 Linux 用户名规则。
    """
    if not username:
        return False
    return re.match(r'^[a-z_][a-z0-9_-]{0,31}$', username) is not None

async def get_user_name_map(db: AsyncSession) -> Dict[int, str]:
    """查询用户 ID 到用户名的映射。

    参数：
    - db：异步数据库会话。

    返回：
    - Dict[int, str]：用户 ID 到用户名的字典。
    """
    stmt = select(models.users.Users.id, models.users.Users.username)
    rows = (await db.execute(stmt)).all()
    return {uid: username for uid, username in rows}


async def get_server_ip_map(db: AsyncSession) -> Dict[int, str]:
    """查询服务器 ID 到服务器 IP 的映射。

    参数：
    - db：异步数据库会话。

    返回：
    - Dict[int, str]：服务器 ID 到 IP 的字典。
    """
    stmt = select(models.pspm.PspmServer.id, models.pspm.PspmServer.ip).where(models.pspm.PspmServer.status != -1)
    rows = (await db.execute(stmt)).all()
    return {sid: ip for sid, ip in rows}


async def is_root_user(db: AsyncSession, *, user_id: int) -> bool:
    """判断用户是否为 root 角色。

    参数：
    - db：异步数据库会话。
    - user_id：用户 ID。

    返回：
    - bool：是否拥有 root 角色。
    """
    return await rbac.is_root_user(db, user_id=user_id)
