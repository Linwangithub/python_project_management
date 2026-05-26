"""项目列表查询服务。

本模块负责项目列表接口的权限范围判断、分页查询和轻量运行状态补充。
接口层只接收 Query 参数并返回响应模型，不直接拼装列表查询逻辑。
"""

from __future__ import annotations

from app import crud, schemas
from app.services.pspm.project_health import inspect_projects_runtime_service


async def list_projects_service(
  session,
  current_user,
  *,
  page: int,
  page_size: int,
  owner_id: int | None = None,
) -> schemas.pspm.ProjectItems:
  """查询当前用户可见的项目列表。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - page：页码。
  - page_size：每页数量。
  - owner_id：root 用户可指定成员筛选；普通用户会强制为自己。

  返回：
  - ProjectItems：分页项目列表，并补充轻量运行状态。
  """
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  actual_owner_id = owner_id if is_root else current_user.id
  result = await crud.projects.get_items(
    session,
    current_user_id=current_user.id,
    is_root=is_root,
    owner_id=actual_owner_id,
    page=page,
    page_size=page_size,
  )
  return await inspect_projects_runtime_service(session, current_user, result)
