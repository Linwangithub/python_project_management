"""权限 CRUD 模块，封装角色、菜单和权限相关数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas


ROOT_ROLE_KEY = 'root'
USER_ROLE_KEY = 'user'


class RbacCRUD:
    """RBAC 权限相关 CRUD 适配器。"""
    async def get_role_by_key(self, db: AsyncSession, *, role_key: str) -> Optional[models.rbac.RbacRole]:
        """
        按角色 key 查询启用状态的角色。
        
        参数：
        - db：数据库会话。
        - role_key：角色唯一标识。
        
        返回：
        - 角色 ORM 对象；不存在时返回 None。
        """
        stmt = select(models.rbac.RbacRole).where(models.rbac.RbacRole.role_key == role_key, models.rbac.RbacRole.status == 1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def bind_user_role(self, db: AsyncSession, *, user_id: int, role_id: int) -> None:
        """
        绑定用户与角色。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        - role_id：角色 ID。
        
        作用：
        - 已存在但禁用的绑定会重新启用；不存在时新建绑定。
        """
        stmt = select(models.rbac.RbacUserRole).where(
            models.rbac.RbacUserRole.user_id == user_id,
            models.rbac.RbacUserRole.role_id == role_id,
        )
        exists = (await db.execute(stmt)).scalar_one_or_none()
        if exists:
            if exists.status != 1:
                await db.execute(
                    update(models.rbac.RbacUserRole)
                    .where(models.rbac.RbacUserRole.id == exists.id)
                    .values(status=1)
                )
        else:
            db.add(models.rbac.RbacUserRole(user_id=user_id, role_id=role_id, status=1))
        await db.commit()

    async def get_user_roles(self, db: AsyncSession, *, user_id: int) -> List[models.rbac.RbacRole]:
        """
        查询用户拥有的有效角色列表。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        
        返回：
        - 角色 ORM 对象列表。
        """
        stmt = (
            select(models.rbac.RbacRole)
            .join(models.rbac.RbacUserRole, models.rbac.RbacUserRole.role_id == models.rbac.RbacRole.id)
            .where(
                models.rbac.RbacUserRole.user_id == user_id,
                models.rbac.RbacUserRole.status == 1,
                models.rbac.RbacRole.status == 1,
            )
            .order_by(models.rbac.RbacRole.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_user_role_keys(self, db: AsyncSession, *, user_id: int) -> List[str]:
        """
        查询用户角色 key 列表。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        
        返回：
        - 角色 key 字符串列表。
        """
        roles = await self.get_user_roles(db, user_id=user_id)
        return [r.role_key for r in roles]

    async def is_root_user(self, db: AsyncSession, *, user_id: int) -> bool:
        """
        判断用户是否拥有 root 角色。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        
        返回：
        - True 表示 root 用户，否则为 False。
        """
        role_keys = await self.get_user_role_keys(db, user_id=user_id)
        return ROOT_ROLE_KEY in role_keys

    async def get_user_permission_rows(self, db: AsyncSession, *, user_id: int) -> List[models.rbac.RbacPermission]:
        """
        查询用户通过角色获得的有效权限行。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        
        返回：
        - 去重后的权限 ORM 对象列表。
        """
        stmt = (
            select(models.rbac.RbacPermission)
            .join(models.rbac.RbacRolePermission, models.rbac.RbacRolePermission.permission_id == models.rbac.RbacPermission.id)
            .join(models.rbac.RbacRole, models.rbac.RbacRole.id == models.rbac.RbacRolePermission.role_id)
            .join(models.rbac.RbacUserRole, models.rbac.RbacUserRole.role_id == models.rbac.RbacRole.id)
            .where(
                models.rbac.RbacUserRole.user_id == user_id,
                models.rbac.RbacUserRole.status == 1,
                models.rbac.RbacRole.status == 1,
                models.rbac.RbacRolePermission.status == 1,
                models.rbac.RbacPermission.status == 1,
            )
            .order_by(models.rbac.RbacPermission.id.asc())
        )
        rows = list((await db.execute(stmt)).scalars().all())
        uniq = {}
        for row in rows:
            uniq[row.id] = row
        return list(uniq.values())

    async def get_user_permission_snapshot(self, db: AsyncSession, *, user: schemas.users.Data) -> schemas.rbac.UserPermissionData:
        """
        构建前端需要的用户权限快照。
        
        参数：
        - db：数据库会话。
        - user：当前用户数据。
        
        返回：
        - 包含角色、菜单、动作权限的 UserPermissionData。
        """
        roles = await self.get_user_roles(db, user_id=user.id)
        role_keys = [r.role_key for r in roles]

        permissions = await self.get_user_permission_rows(db, user_id=user.id)
        menu_name_map: Dict[str, str] = {}
        menu_keys: Set[str] = set()
        action_map: Dict[str, Set[str]] = {}

        for perm in permissions:
            menu_keys.add(perm.menu_key)
            menu_name_map[perm.menu_key] = perm.menu_name
            if perm.action_key:
                action_map.setdefault(perm.menu_key, set()).add(perm.action_key)
            else:
                action_map.setdefault(perm.menu_key, set())

        grants = [
            schemas.rbac.PermissionGrant(
                menu_key=menu_key,
                menu_name=menu_name_map.get(menu_key, menu_key),
                actions=sorted(list(action_map.get(menu_key, set()))),
            )
            for menu_key in sorted(menu_keys)
        ]

        return schemas.rbac.UserPermissionData(
            user_id=user.id,
            username=user.username,
            roles=role_keys,
            menus=sorted(list(menu_keys)),
            permissions={k: sorted(list(v)) for k, v in action_map.items()},
            grants=grants,
        )

    async def has_permission(self, db: AsyncSession, *, user_id: int, menu_key: str, action_key: Optional[str] = None) -> bool:
        """
        判断用户是否拥有指定菜单或动作权限。
        
        参数：
        - db：数据库会话。
        - user_id：用户 ID。
        - menu_key：菜单权限 key。
        - action_key：动作权限 key；为空时检查菜单可见权限。
        
        返回：
        - True 表示有权限，否则 False。
        """
        stmt = (
            select(models.rbac.RbacPermission.id)
            .join(models.rbac.RbacRolePermission, models.rbac.RbacRolePermission.permission_id == models.rbac.RbacPermission.id)
            .join(models.rbac.RbacRole, models.rbac.RbacRole.id == models.rbac.RbacRolePermission.role_id)
            .join(models.rbac.RbacUserRole, models.rbac.RbacUserRole.role_id == models.rbac.RbacRole.id)
            .where(
                models.rbac.RbacUserRole.user_id == user_id,
                models.rbac.RbacUserRole.status == 1,
                models.rbac.RbacRole.status == 1,
                models.rbac.RbacRolePermission.status == 1,
                models.rbac.RbacPermission.status == 1,
                models.rbac.RbacPermission.menu_key == menu_key,
            )
        )

        if action_key is None:
            stmt = stmt.where(models.rbac.RbacPermission.action_key.is_(None))
        else:
            stmt = stmt.where(models.rbac.RbacPermission.action_key == action_key)

        result = await db.execute(stmt.limit(1))
        return result.first() is not None


rbac = RbacCRUD()
