"""项目管理 CRUD 模块，封装项目、服务器、环境和日志的数据访问逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.crud.base import CRUDBase
from app.crud.pspm_helpers import get_server_ip_map, get_user_name_map, project_status_to_name

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
