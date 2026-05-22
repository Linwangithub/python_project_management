from __future__ import annotations

import re
from typing import Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase
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


class CRUDPspmEnv(CRUDBase[models.pspm.PspmEnv, schemas.pspm.EnvCreate, schemas.pspm.EnvUpdate]):
    """环境管理 CRUD 封装。

    用途：
    - 为环境管理接口提供列表查询和批量软删除能力。
    """
    async def get_items(
        self,
        db: AsyncSession,
        *,
        owner_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> schemas.pspm.EnvItems:
        """分页查询环境列表。

        参数：
        - db：异步数据库会话。
        - owner_id：指定用户 ID；为空时查询全部可见环境。
        - page：页码。
        - page_size：每页数量。

        返回：
        - EnvItems：环境总数和环境行数据。
        """
        filters = [models.pspm.PspmEnv.status != -1]
        if owner_id is not None:
            filters.append(models.pspm.PspmEnv.owner_id == owner_id)

        total_stmt = select(func.count()).select_from(models.pspm.PspmEnv).where(*filters)
        total = (await db.execute(total_stmt)).scalar_one()

        stmt = (
            select(models.pspm.PspmEnv)
            .where(*filters)
            .order_by(models.pspm.PspmEnv.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).scalars().all()

        return schemas.pspm.EnvItems(
            total=total,
            data=[
                schemas.pspm.EnvItem(
                    id=row.id,
                    env_name=row.env_name,
                    project_name=row.project_name,
                    python_version=row.python_version,
                    main_packages=row.main_packages,
                    created_at=row.created_at,
                )
                for row in rows
            ],
        )

    async def remove_multi(self, db: AsyncSession, *, ids: List[int]) -> int:
        """批量软删除环境记录。

        参数：
        - db：异步数据库会话。
        - ids：环境 ID 列表。

        返回：
        - int：实际更新行数。
        """
        stmt = (
            update(models.pspm.PspmEnv)
            .where(models.pspm.PspmEnv.id.in_(ids), models.pspm.PspmEnv.status != -1)
            .values(status=-1)
            .execution_options(synchronize_session='fetch')
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0


envs = CRUDPspmEnv(models.pspm.PspmEnv)


class CRUDPspmServer(CRUDBase[models.pspm.PspmServer, schemas.pspm.ServerCreate, schemas.base.Item]):
    """服务器管理 CRUD 封装。

    用途：
    - 为服务器管理接口提供列表、分配用户和软删除能力。
    """
    async def get_items(
        self,
        db: AsyncSession,
        *,
        user_id: Optional[int] = None,
        is_root: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> schemas.pspm.ServerItems:
        """分页查询服务器列表。

        参数：
        - db：异步数据库会话。
        - user_id：当前用户 ID，普通用户按该值过滤可见服务器。
        - is_root：当前用户是否为 root 角色。
        - page：页码。
        - page_size：每页数量。

        返回：
        - ServerItems：服务器总数和服务器行数据。
        """
        filters = [models.pspm.PspmServer.status != -1]

        if (not is_root) and (user_id is not None):
            username_stmt = select(models.users.Users.username).where(models.users.Users.id == user_id)
            username = (await db.execute(username_stmt)).scalar_one_or_none()
            if not username:
                return schemas.pspm.ServerItems(total=0, data=[])

            stmt_all = (
                select(models.pspm.PspmServer)
                .where(models.pspm.PspmServer.status != -1)
                .order_by(models.pspm.PspmServer.id.desc())
            )
            all_servers = (await db.execute(stmt_all)).scalars().all()
            visible_servers = [
                row for row in all_servers
                if username in [x for x in normalize_assigned_users(row.assigned_users).split(',') if x]
            ]
            total = len(visible_servers)
            start = max(page - 1, 0) * page_size
            end = start + page_size
            servers = visible_servers[start:end]
        else:
            total_stmt = select(func.count()).select_from(models.pspm.PspmServer).where(*filters)
            total = (await db.execute(total_stmt)).scalar_one()

            stmt = (
                select(models.pspm.PspmServer)
                .where(*filters)
                .order_by(models.pspm.PspmServer.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            servers = (await db.execute(stmt)).scalars().all()

        return schemas.pspm.ServerItems(
            total=total,
            data=[
                schemas.pspm.ServerItem(
                    id=server.id,
                    alias=server.alias,
                    ip=server.ip,
                    root_password=server.root_password or '',
                    users=normalize_assigned_users(server.assigned_users),
                    remark=server.remark,
                )
                for server in servers
            ],
        )

    async def add_assigned_user(self, db: AsyncSession, *, server_id: int, username: str) -> Optional[int]:
        """为服务器追加已分配用户。

        参数：
        - db：异步数据库会话。
        - server_id：服务器 ID。
        - username：需要追加的用户名。

        返回：
        - Optional[int]：更新行数；服务器不存在时返回 None。
        """
        server = await self.get(db, {'id': server_id, 'status': 1})
        if not server:
            return None

        merged = add_assigned_user(server.assigned_users, username)
        rows = await self.update(db, obj_in={'id': server_id}, data_in={'assigned_users': merged})
        return rows

    async def remove_assigned_user(self, db: AsyncSession, *, server_id: int, username: str) -> Optional[int]:
        """从服务器已分配用户列表中移除用户。

        参数：
        - db：异步数据库会话。
        - server_id：服务器 ID。
        - username：需要移除的用户名。

        返回：
        - Optional[int]：更新行数；服务器不存在时返回 None。
        """
        server = await self.get(db, {'id': server_id, 'status': 1})
        if not server:
            return None

        merged = remove_assigned_user(server.assigned_users, username)
        rows = await self.update(db, obj_in={'id': server_id}, data_in={'assigned_users': merged})
        return rows

    async def assign_users(self, db: AsyncSession, *, server_id: int, user_ids: List[int], created_by: int) -> None:
        """重建服务器与系统用户的关系记录。

        参数：
        - db：异步数据库会话。
        - server_id：服务器 ID。
        - user_ids：需要绑定的系统用户 ID 列表。
        - created_by：操作人用户 ID。

        返回：
        - None。
        """
        await db.execute(
            update(models.pspm.PspmServerUser)
            .where(models.pspm.PspmServerUser.server_id == server_id, models.pspm.PspmServerUser.status != -1)
            .values(status=-1)
        )

        rows = [
            models.pspm.PspmServerUser(server_id=server_id, user_id=uid, created_by=created_by, status=1)
            for uid in sorted(set(user_ids))
        ]
        if rows:
            db.add_all(rows)
        await db.commit()

    async def remove_multi(self, db: AsyncSession, *, ids: List[int]) -> int:
        """批量软删除服务器记录及其用户关系。

        参数：
        - db：异步数据库会话。
        - ids：服务器 ID 列表。

        返回：
        - int：服务器表实际更新行数。
        """
        result = await db.execute(
            update(models.pspm.PspmServer)
            .where(models.pspm.PspmServer.id.in_(ids), models.pspm.PspmServer.status != -1)
            .values(status=-1)
            .execution_options(synchronize_session='fetch')
        )
        await db.execute(
            update(models.pspm.PspmServerUser)
            .where(models.pspm.PspmServerUser.server_id.in_(ids), models.pspm.PspmServerUser.status != -1)
            .values(status=-1)
        )
        await db.commit()
        return result.rowcount or 0


servers = CRUDPspmServer(models.pspm.PspmServer)


class CRUDPspmProject(CRUDBase[models.pspm.PspmProject, schemas.pspm.ProjectCreate, schemas.pspm.ProjectSettingUpdate]):
    """项目管理 CRUD 封装。

    用途：
    - 为项目列表、删除、运行状态更新和归属迁移提供数据库访问能力。
    """
    async def get_items(
        self,
        db: AsyncSession,
        *,
        current_user_id: int,
        is_root: bool,
        owner_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> schemas.pspm.ProjectItems:
        """分页查询项目列表。

        参数：
        - db：异步数据库会话。
        - current_user_id：当前用户 ID。
        - is_root：当前用户是否为 root 角色。
        - owner_id：按项目所属用户过滤，root 可使用。
        - page：页码。
        - page_size：每页数量。

        返回：
        - ProjectItems：项目总数和项目行数据。
        """
        filters = [models.pspm.PspmProject.status.in_([0, 1])]

        if owner_id is not None:
            filters.append(models.pspm.PspmProject.owner_id == owner_id)
        elif not is_root:
            filters.append(models.pspm.PspmProject.owner_id == current_user_id)

        total_stmt = select(func.count()).select_from(models.pspm.PspmProject).where(*filters)
        total = (await db.execute(total_stmt)).scalar_one()

        stmt = (
            select(models.pspm.PspmProject)
            .where(*filters)
            .order_by(models.pspm.PspmProject.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).scalars().all()

        user_name_map = await get_user_name_map(db)
        server_ip_map = await get_server_ip_map(db)

        return schemas.pspm.ProjectItems(
            total=total,
            data=[
                schemas.pspm.ProjectItem(
                    id=row.id,
                    owner_id=row.owner_id,
                    owner=user_name_map.get(row.owner_id, f'user_{row.owner_id}'),
                    name=row.name,
                    description=row.description,
                    server_id=row.server_id,
                    server_ip=server_ip_map.get(row.server_id) if row.server_id else None,
                    backend_path=row.backend_path,
                    frontend_path=row.frontend_path,
                    nginx_conf_path=row.nginx_conf_path,
                    nginx_server_ip=getattr(row, 'nginx_server_ip', None),
                    nginx_config_text=getattr(row, 'nginx_config_text', None),
                    frontend_port=row.frontend_port,
                    backend_dev_port=row.backend_dev_port,
                    backend_deploy_port=row.backend_deploy_port,
                    database_name=row.database_name,
                    database_host=getattr(row, 'database_host', None),
                    database_port=getattr(row, 'database_port', None),
                    database_user=getattr(row, 'database_user', None),
                    database_password=getattr(row, 'database_password', None),
                    conda_env_name=row.conda_env_name,
                    python_version=row.python_version,
                    dev_start_command=row.dev_start_command,
                    deploy_start_command=row.deploy_start_command,
                    entry_file_path=row.entry_file_path,
                    status=project_status_to_name(row.status),
                    service_status=project_status_to_name(row.status),
                    created_at=row.created_at,
                )
                for row in rows
            ],
        )

    async def remove_multi(self, db: AsyncSession, *, ids: List[int]) -> int:
        """批量软删除项目记录。

        参数：
        - db：异步数据库会话。
        - ids：项目 ID 列表。

        返回：
        - int：实际更新行数。
        """
        stmt = (
            update(models.pspm.PspmProject)
            .where(models.pspm.PspmProject.id.in_(ids), models.pspm.PspmProject.status.in_([0, 1]))
            .values(status=-1)
            .execution_options(synchronize_session='fetch')
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0

    async def update_status(self, db: AsyncSession, *, project_id: int, running: bool) -> int:
        """更新项目运行状态。

        参数：
        - db：异步数据库会话。
        - project_id：项目 ID。
        - running：True 表示运行中，False 表示已停止。

        返回：
        - int：实际更新行数。
        """
        stmt = (
            update(models.pspm.PspmProject)
            .where(models.pspm.PspmProject.id == project_id, models.pspm.PspmProject.status.in_([0, 1]))
            .values(status=1 if running else 0)
            .execution_options(synchronize_session='fetch')
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0

    async def transfer_owner(self, db: AsyncSession, *, from_user_ids: List[int], to_user_id: int) -> int:
        """把多个用户的项目归属迁移给指定用户。

        参数：
        - db：异步数据库会话。
        - from_user_ids：原项目所属用户 ID 列表。
        - to_user_id：目标用户 ID。

        返回：
        - int：实际更新行数。
        """
        if not from_user_ids:
            return 0
        stmt = (
            update(models.pspm.PspmProject)
            .where(models.pspm.PspmProject.owner_id.in_(from_user_ids), models.pspm.PspmProject.status.in_([0, 1]))
            .values(owner_id=to_user_id)
            .execution_options(synchronize_session='fetch')
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0


projects = CRUDPspmProject(models.pspm.PspmProject)


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
