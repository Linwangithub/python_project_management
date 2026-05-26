"""项目接口聚合模块，挂载项目查询、校验、同步、管理和运行子路由。

本模块只维护路由聚合关系，具体接口函数按业务类型拆分到 projects_query、projects_check、
projects_sync、projects_manage、projects_runtime 等子模块。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.pspm import projects_check, projects_manage, projects_query, projects_runtime, projects_sync
from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep
from app.services.pspm.project_list import list_projects_service

# 项目管理路由对象：被 app/api/api.py 通过 /pspm/projects 前缀挂载。
router = APIRouter()


@router.get('', name='列表', response_model=schemas.pspm.ProjectItemsResponse)
async def list_projects(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  page: int = Query(1, description='页码'),
  page_size: int = Query(20, description='每页数量'),
  owner_id: int | None = Query(None, description='按用户筛选'),
):
  """查询项目列表。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - page/page_size：分页参数，来自 Query。
  - owner_id：root 用户可按成员筛选；普通用户会被强制改为自己。

  作用：
  - 项目管理主列表使用该接口加载数据。
  - root 看到所有项目，普通用户只能看到自己的项目。

  返回：
  - `ProjectItemsResponse`，包含分页总数和项目行数据。
  """
  result = await list_projects_service(
    session,
    current_user,
    page=page,
    page_size=page_size,
    owner_id=owner_id,
  )
  return schemas.pspm.ProjectItemsResponse(data=result)

router.include_router(projects_query.router)
router.include_router(projects_check.router)
router.include_router(projects_sync.router)
router.include_router(projects_manage.router)
router.include_router(projects_runtime.router)
