"""项目管理 CRUD 模块，封装项目、服务器、环境和日志的数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase

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
