from __future__ import annotations

from typing import Dict, List, Optional, Set

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas


ROOT_ROLE_KEY = 'root'
USER_ROLE_KEY = 'user'


class RbacCRUD:
    async def get_role_by_key(self, db: AsyncSession, *, role_key: str) -> Optional[models.rbac.RbacRole]:
        stmt = select(models.rbac.RbacRole).where(models.rbac.RbacRole.role_key == role_key, models.rbac.RbacRole.status == 1)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def bind_user_role(self, db: AsyncSession, *, user_id: int, role_id: int) -> None:
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
        roles = await self.get_user_roles(db, user_id=user_id)
        return [r.role_key for r in roles]

    async def is_root_user(self, db: AsyncSession, *, user_id: int) -> bool:
        role_keys = await self.get_user_role_keys(db, user_id=user_id)
        return ROOT_ROLE_KEY in role_keys

    async def get_user_permission_rows(self, db: AsyncSession, *, user_id: int) -> List[models.rbac.RbacPermission]:
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
