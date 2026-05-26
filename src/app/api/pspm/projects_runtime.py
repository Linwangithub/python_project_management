"""项目运行路由模块，包含前台启动、后台启动、部署启动、停止、复制和导出。

本模块从项目路由聚合入口拆分而来，只维护同一类项目 HTTP 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

from app.services.pspm.project_runtime import (
  copy_project_service,
  export_project_service,
  finalize_project_foreground_service,
  prepare_project_foreground_service,
  start_project_service,
  stop_project_service,
)
from app.utils.pspm.project_api_messages import (
  MSG_PROJECT_BACKGROUND_STARTED,
  MSG_PROJECT_DEPLOY_STARTED,
  MSG_PROJECT_FOREGROUND_PREPARED,
  MSG_PROJECT_FOREGROUND_STARTED,
  MSG_PROJECT_STOPPED,
)

@router.get('/start-foreground/prepare', name='prepare_start_foreground', response_model=schemas.base.ItemResponse)
async def prepare_start_foreground(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_foreground')),
  project_id: int = Query(..., description='项目ID'),
):
  """准备前台启动参数。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要前台启动权限。
  - project_id：项目 ID。

  作用：
  - 只校验项目、服务器、入口文件、Conda 环境和开发启动命令。
  - 返回前端逐条调用终端执行接口所需的工作目录、环境名、命令和端口。

  返回：
  - ItemResponse.data：前台启动准备信息。
  """
  data = await prepare_project_foreground_service(session, current_user, project_id)
  return schemas.base.ItemResponse(message=MSG_PROJECT_FOREGROUND_PREPARED, data=data)


@router.put('/start-foreground/finalize', name='finalize_start_foreground', response_model=schemas.base.ItemResponse)
async def finalize_start_foreground(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_foreground')),
  payload: schemas.pspm.ProjectForegroundFinalize,
):
  """确认前台启动结果。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要前台启动权限。
  - payload：前端真实终端会话启动后返回的项目 ID、PID 和端口。

  作用：
  - 二次检查进程是否存在、端口是否监听、日志是否有明显错误。
  - 检查通过后写入 runtime 元数据并把项目状态更新为运行中。

  返回：
  - ItemResponse.data：最终启动结果。
  """
  data = await finalize_project_foreground_service(
    session,
    current_user,
    payload.project_id,
    payload.pid,
    payload.port or '',
    payload.log_file or '',
  )
  return schemas.base.ItemResponse(message=(data.get('message') or MSG_PROJECT_FOREGROUND_STARTED), data=data)


@router.put('/start-foreground', name='start_foreground', response_model=schemas.base.ItemResponse)
async def start_foreground(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_foreground')),
  project_id: int = Query(..., description='项目ID'),
):
  """兼容旧版前台启动接口。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要前台启动权限。
  - project_id：项目 ID。

  作用：
  - 保留旧接口，避免旧前端调用报 404。
  - 新前端会优先使用 prepare + terminal execute + finalize 的真实终端流程。

  返回：
  - ItemResponse.data：启动结果。
  """
  data = await start_project_service(session, current_user, project_id, mode='dev', run_in_background=False)
  return schemas.base.ItemResponse(message=(data.get('message') or MSG_PROJECT_FOREGROUND_STARTED), data=data)


@router.put('/start-background', name='start_background', response_model=schemas.base.ItemResponse)
async def start_background(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_background')),
  project_id: int = Query(..., description='项目ID'),
):
  """后台启动项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要后台启动权限。
  - project_id：项目 ID。

  作用：
  - 使用项目设置中的开发启动命令后台启动项目。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - `BaseResponse`，message 为启动结果。
  """
  data = await start_project_service(session, current_user, project_id, mode='dev', run_in_background=True)
  return schemas.base.ItemResponse(message=(data.get('message') or MSG_PROJECT_BACKGROUND_STARTED), data=data)


@router.put('/deploy-start', name='deploy_start', response_model=schemas.base.ItemResponse)
async def deploy_start(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'deploy_start')),
  project_id: int = Query(..., description='项目ID'),
):
  """部署启动项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要部署启动权限。
  - project_id：项目 ID。

  作用：
  - 使用项目设置中的部署启动命令后台启动项目。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - `BaseResponse`，message 为启动结果。
  """
  data = await start_project_service(session, current_user, project_id, mode='deploy', run_in_background=True)
  return schemas.base.ItemResponse(message=(data.get('message') or MSG_PROJECT_DEPLOY_STARTED), data=data)


@router.put('/stop', name='stop_project', response_model=schemas.base.ItemResponse)
async def stop_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'stop')),
  project_id: int = Query(..., description='项目ID'),
):
  """停止项目服务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要停止服务权限。
  - project_id：项目 ID。

  作用：
  - 只停止当前项目 runtime 元数据记录的 PID，避免误杀其他进程。
  - 停止成功后把项目状态更新为已停止。

  返回：
  - `BaseResponse`，message 为停止结果。
  """
  data = await stop_project_service(session, current_user, project_id)
  return schemas.base.ItemResponse(message=(data.get('message') or MSG_PROJECT_STOPPED), data=data)


@router.post('/copy', name='复制', response_model=schemas.base.BaseResponse)
async def copy_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'copy')),
  project_id: int = Query(..., description='项目ID'),
  payload: schemas.pspm.ProjectCopyRequest,
):
  """复制项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要复制权限。
  - project_id：项目 ID。
  - payload：目标服务器和目标目录。

  作用：
  - 当前版本只做权限校验并返回任务提示。
  - 后续真实复制逻辑会放在 service 层，不改接口层。

  返回：
  - `BaseResponse`，message 为复制任务提示。
  """
  message = await copy_project_service(session, current_user, project_id, payload.target_server_ip, payload.target_dir)
  return schemas.base.BaseResponse(message=message)


@router.post('/export', name='导出', response_model=schemas.base.BaseResponse)
async def export_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'export')),
  project_id: int = Query(..., description='项目ID'),
  payload: schemas.pspm.ProjectExportRequest,
):
  """导出项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要导出权限。
  - project_id：项目 ID。
  - payload：导出目录。

  作用：
  - 当前版本只做权限校验并返回任务提示。
  - 后续真实导出逻辑会放在 service 层，不改接口层。

  返回：
  - `BaseResponse`，message 为导出任务提示。
  """
  message = await export_project_service(session, current_user, project_id, payload.target_dir)
  return schemas.base.BaseResponse(message=message)
