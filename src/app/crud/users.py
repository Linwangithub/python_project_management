"""用户 CRUD 模块，封装用户查询、认证、创建和状态判断逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from typing import Any, Dict, Optional, Union, List
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase


class CRUDUsers(CRUDBase[models.users.Users, schemas.users.Create, schemas.users.Update]):

    """用户表 CRUD 适配器。"""
    async def get_multi(self, db: AsyncSession, *, obj_in: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 20, page_break: bool = False) -> schemas.users.Items:
        """
        查询用户列表并返回分页包装结构。
        
        参数：
        - db：数据库会话。
        - obj_in：过滤条件。
        - page/page_size：分页参数。
        - page_break：为 True 时不分页。
        
        返回：
        - schemas.users.Items，包含 total 和 data。
        """
        total = await super().get_total(db, obj_in=obj_in)
        result = await super().get_multi(db, obj_in=obj_in, page=page, page_size=page_size, page_break=page_break)
        return schemas.users.Items(
            total=total,
            data=result,
        )

    async def create(self, db: AsyncSession, *, obj_in: Union[List[Dict[str, Any]], Dict[str, Any]]) -> Union[List[models.users.Users], models.users.Users]:
        """
        创建用户记录。
        
        参数：
        - db：数据库会话。
        - obj_in：单个用户字典或用户字典列表。
        
        返回：
        - 创建后的用户 ORM 对象或对象列表。
        """
        # 按需求：密码明文存储，不做哈希
        return await super().create(db, obj_in=obj_in)

    async def update(self, db: AsyncSession, *, obj_in: Dict[str, Any], data_in: Dict[str, Any]) -> Optional[int]:
        """
        更新用户记录。
        
        参数：
        - db：数据库会话。
        - obj_in：定位条件。
        - data_in：需要更新的字段。
        
        返回：
        - 更新记录数。
        """
        # 按需求：密码明文存储，不做哈希
        return await super().update(db, obj_in=obj_in, data_in=data_in)

    async def remove(self, db: AsyncSession, *, obj_in: Dict[str, Any]) -> Optional[int]:
        """
        按条件软删除或删除用户记录。
        
        参数：
        - db：数据库会话。
        - obj_in：删除过滤条件。
        
        返回：
        - 受影响记录数。
        """
        return await super().remove(db, obj_in=obj_in)

    async def authenticate(self, db: AsyncSession, *, username: str, password: str) -> Optional[schemas.users.Data]:
        """
        校验用户名和密码。
        
        参数：
        - db：数据库会话。
        - username：账号。
        - password：密码。
        
        返回：
        - 校验成功返回用户数据，否则返回 None。
        """
        user = await self.get(db, obj_in={'username': username})
        if not user:
            return None
        if user.password != password:
            return None
        return schemas.users.Data.model_validate(user)

    async def is_active(self, user: schemas.users.Data) -> bool:
        """
        判断用户是否启用。
        
        参数：
        - user：用户数据。
        
        返回：
        - 当前模型默认始终返回 True。
        """
        # 当前模型无status/expired_at字段，默认激活
        return True

    async def next_userid(self, db: AsyncSession) -> int:
        """
        生成下一个业务用户编号。
        
        参数：
        - db：数据库会话。
        
        返回：
        - 当前最大 userid 加一；无用户时返回 1。
        """
        rows = await self.get_multi(db, obj_in={}, page=1, page_size=100000, page_break=True)
        if not rows.data:
            return 1
        return max([x.userid for x in rows.data]) + 1


users = CRUDUsers(models.users.Users)
