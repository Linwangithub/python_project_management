from typing import Any, Dict, Optional, Union, List
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase


class CRUDUsers(CRUDBase[models.users.Users, schemas.users.Create, schemas.users.Update]):

    async def get_multi(self, db: AsyncSession, *, obj_in: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 20, page_break: bool = False) -> schemas.users.Items:
        total = await super().get_total(db, obj_in=obj_in)
        result = await super().get_multi(db, obj_in=obj_in, page=page, page_size=page_size, page_break=page_break)
        return schemas.users.Items(
            total=total,
            data=result,
        )

    async def create(self, db: AsyncSession, *, obj_in: Union[List[Dict[str, Any]], Dict[str, Any]]) -> Union[List[models.users.Users], models.users.Users]:
        # 按需求：密码明文存储，不做哈希
        return await super().create(db, obj_in=obj_in)

    async def update(self, db: AsyncSession, *, obj_in: Dict[str, Any], data_in: Dict[str, Any]) -> Optional[int]:
        # 按需求：密码明文存储，不做哈希
        return await super().update(db, obj_in=obj_in, data_in=data_in)

    async def remove(self, db: AsyncSession, *, obj_in: Dict[str, Any]) -> Optional[int]:
        return await super().remove(db, obj_in=obj_in)

    async def authenticate(self, db: AsyncSession, *, username: str, password: str) -> Optional[schemas.users.Data]:
        user = await self.get(db, obj_in={'username': username})
        if not user:
            return None
        if user.password != password:
            return None
        return schemas.users.Data.model_validate(user)

    async def is_active(self, user: schemas.users.Data) -> bool:
        # 当前模型无status/expired_at字段，默认激活
        return True

    async def next_userid(self, db: AsyncSession) -> int:
        rows = await self.get_multi(db, obj_in={}, page=1, page_size=100000, page_break=True)
        if not rows.data:
            return 1
        return max([x.userid for x in rows.data]) + 1


users = CRUDUsers(models.users.Users)
