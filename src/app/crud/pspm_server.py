"""项目管理 CRUD 模块，封装项目、服务器、环境和日志的数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase
from app.crud.pspm_helpers import add_assigned_user, normalize_assigned_users, remove_assigned_user

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
