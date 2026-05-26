"""项目查询路由模块，包含列表、详情、日志、健康检测和设置辅助查询。

本模块从项目路由聚合入口拆分而来，只维护同一类项目 HTTP 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

from app.services.pspm.project_checks import (
  list_project_conda_envs_service,
  list_project_entry_path_children_service,
)
from app.services.pspm.project_health import inspect_project_health_service
from app.services.pspm.project_detail import get_project_detail_service, list_project_logs_service

@router.get('/entry-path-children', name='入口文件路径子项', response_model=schemas.pspm.ProjectEntryPathChildrenResponse)
async def list_project_entry_path_children(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
  rel_path: str = Query('', description='相对项目根路径'),
):
  """获取入口文件选择器的子目录/文件。

  参数：
  - session：数据库会话，由 FastAPI 依赖 `SessionDep` 注入。
  - current_user：当前登录用户，由 `require_permission('project_management', 'setting')` 校验权限后注入。
  - project_id：项目 ID，来自 Query 参数，用于定位项目根目录。
  - rel_path：相对项目根目录路径，来自前端级联选择器当前节点。

  作用：
  - 设置弹框中选择项目入口文件时，前端逐层请求该接口展示下一层目录和文件。

  返回：
  - `ProjectEntryPathChildrenResponse`，data 为可选节点列表。
  """
  nodes = await list_project_entry_path_children_service(session, current_user, project_id, rel_path)
  return schemas.pspm.ProjectEntryPathChildrenResponse(data=nodes)


@router.get('/health', name='项目健康检测', response_model=schemas.pspm.ProjectHealthCheckResponse)
async def check_project_health(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """按需检测单个项目健康状态。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自项目状态列按钮。

  作用：
  - 避免项目列表刷新时批量检测所有项目导致页面慢或某个项目异常拖垮整个列表。
  - 点击某一行“检测状态”按钮时，只检测当前项目。

  返回：
  - ProjectHealthCheckResponse，data 为带检测结果的项目行结构。
  """
  data = await inspect_project_health_service(session, current_user, project_id)
  return schemas.pspm.ProjectHealthCheckResponse(data=data)


@router.get('/detail', name='项目详情', response_model=schemas.pspm.ProjectDetailResponse)
async def get_project_detail(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目完整详情。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自详情按钮所在行。

  作用：
  - 详情侧边栏调用该接口展示项目所有已配置的信息。
  - 包含 Conda 环境位置、Conda 中 Python 版本、数据库、Nginx、启动命令等信息。

  返回：
  - `ProjectDetailResponse`，data.sections 为前端分组展示数据。
  """
  data = await get_project_detail_service(session, current_user, project_id)
  return schemas.pspm.ProjectDetailResponse(data=data)


@router.get('/logs', name='项目操作日志', response_model=schemas.pspm.ProjectOperationLogsResponse)
async def list_project_logs(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目操作日志。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自日志按钮所在行。

  作用：
  - 日志弹框调用该接口展示项目创建、配置修改、启动停止等操作记录。
  - 修改配置日志会包含修改前、修改后和字段变化明细。

  返回：
  - `ProjectOperationLogsResponse`，包含操作日志列表。
  """
  data = await list_project_logs_service(session, current_user, project_id)
  return schemas.pspm.ProjectOperationLogsResponse(data=data)


@router.get('/conda-envs', name='查询Conda环境列表', response_model=schemas.pspm.ProjectCondaEnvListResponse)
async def list_project_conda_envs(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目所在服务器的 Conda 环境列表。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目设置权限。
  - project_id：项目 ID，来自 Query。

  作用：
  - 设置弹框 Conda 步骤用于展示已有环境。
  - 前端也根据该列表判断用户输入的新环境名是否冲突。

  返回：
  - `ProjectCondaEnvListResponse`，包含 Conda 环境目录和环境名列表。
  """
  data = await list_project_conda_envs_service(session, current_user, project_id)
  return schemas.pspm.ProjectCondaEnvListResponse(data=data)
